# TwoKeys — Architecture Specification
**Version:** 1.0
**Track:** Agents for Business — Kaggle 5-Day AI Agent Capstone
**Status:** Production-ready (local deployment)

---

## 1. Problem Statement

Enterprise expense approval systems that rely on a single AI agent inherit a fundamental weakness: one compromised or hallucinating agent can approve fraudulent requests, violate spending policy, or be manipulated by prompt injection embedded in expense descriptions. A single agent has no adversarial check on its own reasoning.

**TwoKeys solves this by requiring two independent AI agents to agree before any expense is approved** — mirroring the dual-control principle used in banking, nuclear facilities, and cryptographic key management, where no single individual can authorize a sensitive action alone.

---

## 2. Design Goals

| Goal | Rationale |
|------|-----------|
| Two independent decision-makers | No single agent failure can approve or block a request unilaterally |
| Independence enforced architecturally | Auditor must not see Builder's decision before forming its own |
| Deterministic safety boundary | Disagreement comparison in code, not LLM judgment |
| Fail-safe on infrastructure failure | Agent unavailability escalates to human, never silently defaults |
| Long-term memory of vendor behavior | Past violations inform future scrutiny |
| Legible audit trail | Every decision logged with full reasoning for compliance review |

---

## 3. System Overview

TwoKeys is built on the **Google ADK 2.0 graph Workflow API**. The system is a directed graph of nodes where each node is either a deterministic Python function or an LLM-backed `LlmAgent`. Edges define routing logic based on node output.

### 3.1 Agent Roles

**Builder Agent** (`builder_agent`)
- Role: First-pass policy evaluator
- Model: `gemini-2.0-flash`
- Input: Parsed expense request (vendor, amount, description, vendor_blocked flag, vendor history)
- Output: Structured `BuilderDecision` — `{action: approve|deny, amount, reasoning}`
- Constraint: Must not receive any prior routing decision or human hint

**Auditor Agent** (`auditor_agent`)
- Role: Independent second reviewer
- Model: `gemini-2.0-flash`
- Input: Same parsed expense request as Builder — **never Builder's decision**
- Output: Structured `AuditorVerdict` — `{verdict: approve|deny|escalate, reasoning}`
- Constraint: Prompt explicitly instructs re-derivation from scratch, not validation of Builder

**Key independence property:** Both agents receive identical input facts but in separate LLM calls with no shared state. The comparison of their outputs happens in deterministic Python (`route_auditor_verdict`), not in a third LLM call.

---

## 4. Graph Specification

### 4.1 Nodes

| Node | Type | Responsibility |
|------|------|---------------|
| `START` | Built-in | Entry point; receives raw user message |
| `intake` | Python function | Parse JSON/text input; vendor blocked check; vendor history lookup; generate request_id |
| `run_builder` | Python wrapper | Invoke `builder_agent` with retry-backoff; handle failure → set `builder_failed` flag |
| `run_auditor` | Python wrapper | Invoke `auditor_agent` with retry-backoff; handle failure → set `auditor_failed` flag |
| `route_auditor_verdict` | Python function | Compare Builder + Auditor decisions; route based on agreement/disagreement/failure |
| `human_escalation` | Async generator | Issue `RequestInput` to pause workflow; resume on human response; log outcome |
| `approved` | Python function | Log approved decision to SQLite; return formatted approval message |
| `rejected` | Python function | Log rejected decision to SQLite; return formatted rejection with reasoning |

### 4.2 Edges

```
START
  └──► intake
         ├── amount < $100  ──────────────────────────────────► approved
         └── amount >= $100 ──► run_builder ──► run_auditor ──► route_auditor_verdict
                                                                      ├── confirm ──► approved
                                                                      ├── block ───► rejected
                                                                      └── escalate ► human_escalation
                                                                                          ├── yes ──► approved
                                                                                          └── no  ──► rejected
```

### 4.3 Routing Logic in `route_auditor_verdict`

```python
# Pseudo-code — actual implementation in app/agent.py
if builder_failed or auditor_failed:
    route = "escalate"  # fail-safe: infrastructure failure → human
elif builder_decision == "deny" or auditor_verdict == "deny":
    if builder_decision != auditor_verdict:
        route = "escalate"  # disagreement → human
    else:
        route = "block"     # both deny → rejected
elif builder_decision == "approve" and auditor_verdict == "approve":
    route = "confirm"       # both approve → approved
else:
    route = "escalate"      # any ambiguity → human
```

---

## 5. Data Models

### 5.1 Session State (passed between nodes via `ctx.state`)

```python
{
  "expense_request": {
    "vendor": str,
    "amount": float,
    "description": str,
    "vendor_blocked": bool,      # deterministic lookup against BLOCKED_VENDORS set
    "vendor_history": str        # compact summary from SQLite (token-efficient)
  },
  "request_id": str,             # UUID4, generated at intake
  "builder_decision": {
    "action": "approve" | "deny",
    "amount": float,
    "reasoning": str
  },
  "auditor_verdict": {
    "verdict": "approve" | "deny" | "escalate",
    "reasoning": str
  },
  "builder_failed": bool,        # set by run_builder on unrecoverable error
  "auditor_failed": bool,        # set by run_auditor on unrecoverable error
  "failure_message": str         # error detail shown to human escalation reviewer
}
```

### 5.2 Persistent Memory Schema (SQLite)

```sql
CREATE TABLE IF NOT EXISTS expense_decisions (
    request_id      TEXT PRIMARY KEY,
    timestamp       TEXT,           -- ISO 8601 UTC
    vendor          TEXT,
    amount          REAL,
    description     TEXT,
    builder_decision TEXT,          -- JSON serialized BuilderDecision
    auditor_verdict  TEXT,          -- JSON serialized AuditorVerdict
    final_outcome   TEXT            -- "approved" | "rejected" | "escalated"
);
```

### 5.3 Policy Constants

```python
BLOCKED_VENDORS = {"BlockedInc", "Shady Supplies Co", "Offshore Holdings LLC"}
SPENDING_LIMIT  = 5000.0   # single-expense cap
AUTO_APPROVE_THRESHOLD = 100.0  # below this, skip LLM review entirely
REPEAT_REJECTION_THRESHOLD = 2  # vendor with this many rejections gets extra scrutiny
```

---

## 6. Security Specification

### 6.1 Threat Model (STRIDE)

| Threat | Attack Vector | Mitigation |
|--------|--------------|------------|
| **Spoofing** | Fake vendor name claiming approval | Exact-match blocklist lookup in deterministic code, not LLM inference |
| **Tampering** | Injected instructions in expense description | Both agents trained to identify description-as-instruction as a policy violation; injection caught independently by each agent |
| **Repudiation** | "I didn't submit that" | Every request logged with timestamp, full input, and both agents' reasoning |
| **Information Disclosure** | PII in expense descriptions | `vendor_history` summary is compact and anonymized; no raw past descriptions re-exposed |
| **Elevation of Privilege** | "CEO override" social engineering | Auditor sees raw facts only, not Builder's conclusion; Builder explicitly instructed overrides are not valid justifications |
| **Denial of Service** | Agent API unavailability (503/429) | Retry-with-backoff (3 attempts); on failure, escalate to human with warning — never auto-approve or crash silently |

### 6.2 Guardrails

- **No agent can approve unilaterally** — routing logic requires explicit agreement between two separate LLM calls
- **Agent failure is not silent** — `builder_failed` / `auditor_failed` flags force escalation; human reviewer sees warning
- **Vendor blocklist is not LLM-inferred** — `vendor_blocked: bool` is set by deterministic Python before agents are invoked
- **Disagreement is forced escalation** — even if neither agent explicitly says "escalate," conflicting verdicts are treated as escalation by routing code

---

## 7. Memory & Skills Architecture

### 7.1 Memory Module (`app/memory.py`)

Packaged as a reusable ADK skill with three public functions:

| Function | Purpose |
|----------|---------|
| `init_db()` | Create `expense_decisions` table if not exists; called defensively before every read/write |
| `save_decision(...)` | `INSERT OR REPLACE` a completed decision record; called at every terminal node |
| `get_vendor_history(vendor)` | Query and return compact summary dict; called at `intake` before agents run |

**Token efficiency design:** `get_vendor_history` returns a single summary string (`"Vendor X: 3 requests, 2 rejections, most recent: rejected"`) rather than raw rows. This keeps the Auditor's context window lean regardless of how many past records exist for a vendor.

### 7.2 Skill Separation

`memory.py` is kept deliberately separate from `agent.py` so it can be imported, tested, and reused independently. `test_memory.py` tests all three functions in isolation without starting the ADK workflow.

---

## 8. Evaluation Specification

### 8.1 Test Categories

| Category | Count | What it validates |
|----------|-------|------------------|
| Auto-approve | 2 | Low-value clean requests bypass LLM correctly |
| Review-approve | 2 | High-value legitimate requests approved by both agents |
| Deny — blocked vendor | 1 | Blocklist enforcement caught by both agents |
| Deny — over limit | 1 | Spending cap enforcement caught by both agents |
| Adversarial — injection | 2 | Prompt injection / social engineering caught by both agents |
| Disagreement — history | 1 | Vendor with 2 past rejections triggers escalation via memory |
| Borderline escalation | 1 | Weak justification triggers human review |

### 8.2 LLM-as-Judge Scoring

Each case is scored by a separate Gemini call (`llm_judge.py`) on two dimensions:

**Correctness** — did the actual outcome match the expected outcome? (`yes` / `no`)

**Reasoning quality** (1–5 scale):
- 5 = specific policy citation, exact violation identified
- 4 = correct outcome, reasoning sound but generic
- 3 = correct outcome, missed a key policy detail
- 2 = wrong outcome, partial understanding shown
- 1 = wrong outcome or circular/irrelevant reasoning

**Summary metrics reported:**
- Overall pass rate (%)
- Average reasoning score (/5.00)
- Adversarial catch rate (%) — most important metric for security story

### 8.3 Eval Isolation

The eval pipeline uses a separate `twokeys_eval.db` database (set via `TWOKEYS_DB_PATH` environment variable) to avoid polluting production decision logs. The eval DB is wiped and re-seeded at the start of each run.

---

## 9. Observability

### 9.1 ADK Dev UI

The ADK playground (`http://127.0.0.1:8080/dev-ui/?app=app`) provides built-in observability:

- **Events panel** — full node-by-node execution trace per request, including state snapshots at each step
- **Traces panel** — timeline view of agent execution with latency per node
- **State tab** — inspect full session state at any point in the workflow
- **Evals tab** — run and review evaluation cases directly in the UI

### 9.2 SQLite Audit Log

`twokeys_memory.db` serves as a persistent audit trail. Every approved, rejected, and escalated decision is logged with full Builder and Auditor reasoning, enabling post-hoc analysis of agent behavior patterns.

---

## 10. Deployment

### 10.1 Local (Development)

```bash
uv run adk web app --host 127.0.0.1 --port 8080 --reload_agents
```

### 10.2 Public Demo (ngrok)

```bash
# Terminal 1: start local server
uv run adk web app --host 127.0.0.1 --port 8080

# Terminal 2: expose publicly
ngrok http 8080
```

### 10.3 Production Path (Cloud Run)

The ADK scaffold includes a `deployment/` directory with Cloud Run configuration. Production deployment would require:
- Google Cloud project with billing enabled
- `gcloud run deploy` via the `google-agents-cli-deploy` skill
- Environment variables for `GOOGLE_API_KEY` and `TWOKEYS_DB_PATH` (pointing to Cloud SQL or GCS-mounted SQLite)

---

## 11. Known Limitations

| Limitation | Impact | Mitigation / Future Work |
|------------|--------|-------------------------|
| Gemini free-tier quota (20 req/day for 2.5-flash) | Eval pipeline must run on fresh daily quota | Switch to paid tier or `gemini-2.0-flash` (1500/day) for sustained use |
| SQLite not suitable for multi-instance deployment | Single-file DB doesn't scale horizontally | Replace with Cloud SQL (Postgres) for production |
| Blocked vendor list is hardcoded | Adding vendors requires code change | Move to DB-backed admin panel |
| Human escalation requires manual response | Blocks workflow until someone responds | Add timeout → auto-escalate-to-senior-reviewer path |

---

*TwoKeys — Built for the Kaggle 5-Day AI Agent Capstone, Agents for Business track.*
*Architecture designed and implemented by a solo developer using spec-driven vibe coding in Antigravity IDE.*
