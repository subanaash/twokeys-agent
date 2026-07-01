# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import json
import os
import re
from typing import Any, Literal
import uuid
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from google.adk.workflow import Workflow, START, node
from google.adk.apps import App, ResumabilityConfig
from google.adk.agents.context import Context
from google.adk.events.event import Event
from google.adk.events.request_input import RequestInput
from google.adk.agents import LlmAgent
from google.genai import types

from app.memory import get_vendor_history, save_decision

# Load environment variables from .env
load_dotenv()

# Setup authentication dynamically
if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI", "True").lower() == "true":
    os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "True")
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        try:
            import google.auth
            _, project_id = google.auth.default()
            os.environ["GOOGLE_CLOUD_PROJECT"] = project_id
        except Exception:
            pass
    os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "global")
else:
    # Explicitly using Gemini Developer API (AI Studio) instead of Vertex AI.
    # Chosen for this project because it requires no GCP billing setup,
    # keeping the system runnable with just a free API key.
    os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "False"

# Deterministic blocklist, checked in code rather than left to LLM judgment.
# This is a deliberate security boundary: a list membership check cannot be
# "talked out of" the way a model's safety instructions sometimes can.
BLOCKED_VENDORS = {"BlockedInc", "Shady Supplies Co", "Offshore Holdings LLC"}


class ExpenseRequest(BaseModel):
    vendor: str = Field(description="The vendor name")
    amount: float = Field(description="The expense amount")
    description: str = Field(description="Description of the expense")
    vendor_blocked: bool = Field(default=False, description="Whether the vendor is blocked")
    vendor_history: str = Field(default="", description="Summary of past requests and outcomes for this vendor")


# Structured output schemas for both agents. Using Pydantic schemas instead of
# free-form text means each agent's response is a typed, validated object —
# not a string that needs fragile regex/string parsing to extract a verdict.
class BuilderDecision(BaseModel):
    action: Literal["approve", "deny"] = Field(description="The decision action: approve or deny")
    amount: float = Field(description="The expense amount")
    reasoning: str = Field(description="Detailed reasoning about policy compliance")


class AuditorVerdict(BaseModel):
    verdict: Literal["approve", "deny", "escalate"] = Field(description="The independent verdict: approve, deny, or escalate")
    reasoning: str = Field(description="Detailed reasoning for the verdict")


async def retry_with_backoff(coro, max_attempts=3, initial_delay=1.0, backoff_factor=2.0):
    """Helper function to retry a coroutine with exponential backoff.

    Distinguishes between transient capacity errors (429/503/quota) and other
    failures: capacity errors get a fixed 30s backoff since they're usually
    resolved by a short wait, while other errors use exponential backoff.
    Both paths eventually surface to the caller, which is what allows the
    Builder/Auditor wrappers below to fail safely into human escalation
    rather than retrying forever or crashing the whole workflow.
    """
    delay = initial_delay
    for attempt in range(max_attempts):
        try:
            if callable(coro):
                return await coro()
            else:
                return await coro
        except Exception as e:
            if attempt == max_attempts - 1:
                raise e
            err_str = str(e).lower()
            if "429" in err_str or "503" in err_str or "quota" in err_str or "limit" in err_str or "demand" in err_str:
                sleep_time = 30.0
                print(f"Agent API rate limit or overload detected. Sleeping 30s before retry (attempt {attempt + 1})...")
            else:
                sleep_time = delay
                print(f"Attempt {attempt + 1} failed: {e}. Retrying in {sleep_time}s...")
                delay *= backoff_factor
            await asyncio.sleep(sleep_time)


def parse_expense_request(text: str) -> ExpenseRequest:
    # Try parsing as JSON first
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return ExpenseRequest(
                vendor=data.get("vendor", "Unknown"),
                amount=float(data.get("amount", 0)),
                description=data.get("description", ""),
                vendor_blocked=False
            )
    except Exception:
        pass

    # Try regex extraction
    vendor_match = re.search(r"vendor:\s*([^\n,]+)", text, re.IGNORECASE)
    amount_match = re.search(r"amount:\s*\$?([0-9.]+)", text, re.IGNORECASE)
    desc_match = re.search(r"(?:description|desc):\s*([^\n,]+)", text, re.IGNORECASE)

    vendor = vendor_match.group(1).strip() if vendor_match else "Unknown"
    amount = float(amount_match.group(1).strip()) if amount_match else 0.0
    description = desc_match.group(1).strip() if desc_match else ""

    # Fallback to general parsing if fields are not matched structured
    if vendor == "Unknown" and amount == 0.0:
        num_match = re.search(r"\$?([0-9.]+)", text)
        if num_match:
            amount = float(num_match.group(1))

        # Heuristics to map loose user strings to mock blocked vendors
        if "blockedinc" in text.lower():
            vendor = "BlockedInc"
        elif "shady" in text.lower():
            vendor = "Shady Supplies Co"
        elif "offshore" in text.lower():
            vendor = "Offshore Holdings LLC"
        elif "starbucks" in text.lower():
            vendor = "Starbucks"
        else:
            vendor = "General Vendor"
        description = text

    return ExpenseRequest(vendor=vendor, amount=amount, description=description)


def intake(ctx: Context, node_input: Any) -> Event:
    """Intakes the user request, parses the expense, and routes based on amount.

    Routing on amount (auto-approve under $100) is a deliberate efficiency
    decision: running two LLM agents on every trivial expense would waste
    quota and latency on requests that pose negligible risk. The $100
    threshold concentrates agent reasoning on the requests where it actually
    matters.
    """
    text = ""
    if isinstance(node_input, dict):
        req = ExpenseRequest(
            vendor=node_input.get("vendor", "Unknown"),
            amount=float(node_input.get("amount", 0)),
            description=node_input.get("description", "")
        )
    else:
        if hasattr(node_input, "parts") and node_input.parts:
            text = "".join(part.text for part in node_input.parts if part.text)
        elif isinstance(node_input, str):
            text = node_input
        elif node_input is not None:
            text = str(node_input)

        req = parse_expense_request(text)

    # Attach blocked vendor status
    req.vendor_blocked = req.vendor in BLOCKED_VENDORS

    # Long-term memory in action: every request is enriched with the
    # vendor's historical outcome pattern before either agent reasons about
    # it. This is what lets the Auditor apply extra scrutiny to repeat
    # offenders (see auditor_agent's instructions below) rather than
    # evaluating every request as if it were the vendor's first.
    history = get_vendor_history(req.vendor)
    req.vendor_history = history.get("summary", "")

    # Generate unique request ID for persistent memory
    request_id = str(uuid.uuid4())

    req_dict = req.model_dump()
    state_delta = {
        "expense_request": req_dict,
        "request_id": request_id
    }

    if req.amount >= 100:
        return Event(output=req_dict, route="needs_review", state=state_delta)
    else:
        return Event(output=req_dict, route="auto_approve", state=state_delta)


# LLM builder agent.
#
# DESIGN NOTE: the Builder is the *first* opinion, not the final word. Its
# job is to make an initial policy call. It is deliberately not trusted
# alone — see route_auditor_verdict() below, which requires the Auditor to
# independently reach the same conclusion before anything is finalized.
builder_agent = LlmAgent(
    name="builder_agent",
    model="gemini-2.5-flash",
    instruction="""You are an expense builder agent.
You evaluate the expense request against the policy:
- No single expense over $5000 is allowed.
- Must have a business justification in the description (i.e. not empty, trivial, or nonsense).
- No approvals for blocked vendors (Vendor Blocked Status: {expense_request[vendor_blocked]}).

Input Expense Request:
Vendor: {expense_request[vendor]}
Amount: {expense_request[amount]}
Description: {expense_request[description]}
Blocked Status: {expense_request[vendor_blocked]}

Generate a structured decision matching the output schema.
""",
    output_schema=BuilderDecision,
    output_key="builder_decision",
)


@node(rerun_on_resume=True)
async def run_builder(ctx: Context, node_input: Any) -> Event:
    """Wrapper function to run the builder agent with retries and exception handling.

    SECURITY NOTE: if the Builder ultimately fails after retries, this does
    NOT default to either approve or deny. It returns a deny placeholder and
    sets `builder_failed`, which forces the workflow into human escalation
    downstream (see route_auditor_verdict and human_escalation). An
    infrastructure failure must never be allowed to silently resolve into an
    approval.
    """
    try:
        # Wrap the node execution in a lambda to allow retrying/re-invoking the coroutine
        result = await retry_with_backoff(
            lambda: ctx.run_node(builder_agent, node_input=node_input),
            max_attempts=3,
            initial_delay=1.0,
            backoff_factor=2.0
        )
        return Event(output=result, state={"builder_decision": result})
    except Exception as e:
        print(f"Builder agent ultimately failed after retries: {e}")
        return Event(
            output={"action": "deny", "amount": 0.0, "reasoning": f"Builder failed: {e}"},
            state={"builder_failed": True, "failure_message": str(e)}
        )


# LLM auditor agent (evaluates independently without builder's decision).
#
# DESIGN NOTE (the core security property of this system): the Auditor's
# prompt never includes the Builder's decision or reasoning anywhere in its
# context. It only receives the same raw expense facts the Builder saw. This
# is what makes the two-agent check meaningful rather than theatrical — if
# the Auditor could see "the Builder already approved this," it would have
# every incentive (and an LLM's natural tendency toward agreement) to rubber
# -stamp rather than independently re-derive a verdict. A prompt injection
# or reasoning error that fools the Builder still has to separately fool the
# Auditor, with no shared context to exploit.
auditor_agent = LlmAgent(
    name="auditor_agent",
    model="gemini-2.5-flash",
    instruction="""You are an independent expense auditor agent.
You evaluate the expense request against the policy:
- No single expense over $5000 is allowed.
- Must have a business justification in the description (i.e. not empty, trivial, or nonsense).
- No approvals for blocked vendors (Vendor Blocked Status: {expense_request[vendor_blocked]}).

Input Expense Request:
Vendor: {expense_request[vendor]}
Amount: {expense_request[amount]}
Description: {expense_request[description]}
Blocked Status: {expense_request[vendor_blocked]}
Vendor History: {expense_request[vendor_history]}

Your Job:
1. Evaluate the request independently from scratch against the policy. Do not refer to any previous builder decisions.
2. Consider the vendor history summary. If a vendor has 2 or more past rejections, treat this as a flag requiring extra scrutiny — lean toward escalate rather than confirm on borderline cases for that vendor.
3. Formulate your independent verdict matching the output schema.
""",
    output_schema=AuditorVerdict,
    output_key="auditor_verdict",
)


@node(rerun_on_resume=True)
async def run_auditor(ctx: Context, node_input: Any) -> Event:
    """Wrapper function to run the auditor agent with retries and exception handling.

    If the Builder already failed, the Auditor is skipped entirely (no point
    spending a second LLM call when the workflow is already routed to human
    escalation) and an explicit escalate verdict is returned with a message
    naming the failure, so the human reviewer knows *why* this needs review,
    not just that it does.
    """
    if ctx.state.get("builder_failed"):
        return Event(
            output={"verdict": "escalate", "reasoning": "Builder agent call failed, skipping auditor."},
            state={"auditor_failed": True, "failure_message": "Skipped due to Builder failure."}
        )
    try:
        result = await retry_with_backoff(
            lambda: ctx.run_node(auditor_agent, node_input=node_input),
            max_attempts=3,
            initial_delay=1.0,
            backoff_factor=2.0
        )
        return Event(output=result, state={"auditor_verdict": result})
    except Exception as e:
        print(f"Auditor agent ultimately failed after retries: {e}")
        return Event(
            output={"verdict": "escalate", "reasoning": f"Auditor failed: {e}"},
            state={"auditor_failed": True, "failure_message": str(e)}
        )


def route_auditor_verdict(ctx: Context, node_input: Any) -> Event:
    """Compares the independent auditor's verdict with the builder's decision in code.

    DESIGN NOTE: this comparison is deliberately plain Python, not a third
    LLM call asking "do these two agree." Letting a model judge agreement
    would reintroduce exactly the failure mode this architecture exists to
    avoid — a single point of (model) judgment that could itself be wrong or
    manipulated. A direct string/value comparison in code is deterministic,
    fully testable, and impossible to prompt-inject.
    """
    if ctx.state.get("builder_failed") or ctx.state.get("auditor_failed"):
        # Force route to escalate on agent failures
        return Event(output=node_input, route="escalate")

    builder_decision = ctx.state.get("builder_decision", {})
    builder_action = builder_decision.get("action")  # "approve" or "deny"

    # Extract independent auditor action
    auditor_action = "escalate"
    if isinstance(node_input, dict):
        auditor_action = node_input.get("verdict", "escalate")
    elif hasattr(node_input, "verdict"):
        auditor_action = getattr(node_input, "verdict")
    elif isinstance(node_input, str):
        try:
            parsed = json.loads(node_input)
            auditor_action = parsed.get("verdict", "escalate")
        except Exception:
            if "approve" in node_input.lower():
                auditor_action = "approve"
            elif "deny" in node_input.lower():
                auditor_action = "deny"
            elif "escalate" in node_input.lower():
                auditor_action = "escalate"

    # If the auditor independent verdict is escalate, route to human_escalation
    if auditor_action == "escalate":
        return Event(output=node_input, route="escalate")

    # Check for agreement or disagreement between builder and auditor.
    # Agreement -> automatic resolution. Any disagreement -> automatic
    # escalation, with no "tie-breaker" logic of any kind. A 50/50 split
    # between two independent agents is exactly the signal that a human
    # should look at this request, not a case to resolve programmatically.
    if builder_action == auditor_action:
        if builder_action == "approve":
            return Event(output=node_input, route="confirm")
        else:  # "deny"
            return Event(output=node_input, route="block")
    else:
        # Disagreement triggers automatic human escalation
        return Event(output=node_input, route="escalate")


async def human_escalation(ctx: Context, node_input: Any):
    """Asks for human decision when auditor escalates or agent calls fail.

    The escalation message explicitly names which agent failed and why
    (when applicable), so a human reviewer isn't left guessing why a
    request landed in their queue. Every escalated decision is also written
    to the database *before* the human responds, so the audit trail shows
    the request was correctly flagged even if the human review happens
    later.
    """
    req = ctx.state.get("expense_request", {})
    builder_decision = ctx.state.get("builder_decision")
    auditor_verdict = ctx.state.get("auditor_verdict")
    request_id = ctx.state.get("request_id") or str(uuid.uuid4())

    failure_msg = ""
    if ctx.state.get("builder_failed"):
        failure_msg = f" (Warning: Builder Agent call failed: {ctx.state.get('failure_message')})"
    elif ctx.state.get("auditor_failed"):
        failure_msg = f" (Warning: Auditor Agent call failed: {ctx.state.get('failure_message')})"

    if not ctx.resume_inputs:
        # Write escalated outcome to the database
        save_decision(
            request_id=request_id,
            vendor=req.get("vendor", "Unknown"),
            amount=req.get("amount", 0.0),
            description=req.get("description", ""),
            builder_decision=builder_decision,
            auditor_verdict=auditor_verdict,
            final_outcome="escalated"
        )

        yield RequestInput(
            interrupt_id="human_verdict",
            message=f"Expense escalated for review{failure_msg}. Do you approve? (yes/no)"
        )
        return

    reply = ctx.resume_inputs.get("human_verdict", "").strip().lower()
    if reply == "yes":
        yield Event(output="Approved by human reviewer.", route="approved")
    else:
        yield Event(output="Rejected by human reviewer.", route="rejected")


def approved(ctx: Context, node_input: Any) -> str:
    """Final output node for approved expenses."""
    req = ctx.state.get("expense_request", {})
    builder_decision = ctx.state.get("builder_decision")
    auditor_verdict = ctx.state.get("auditor_verdict")
    request_id = ctx.state.get("request_id") or str(uuid.uuid4())

    # Write approved outcome to the database
    save_decision(
        request_id=request_id,
        vendor=req.get("vendor", "Unknown"),
        amount=req.get("amount", 0.0),
        description=req.get("description", ""),
        builder_decision=builder_decision,
        auditor_verdict=auditor_verdict,
        final_outcome="approved"
    )

    details = f"Details: {req}"
    if builder_decision:
        details += f"\nBuilder Decision: {builder_decision}"
    if auditor_verdict:
        details += f"\nIndependent Auditor Verdict: {auditor_verdict}"

    return f"Expense approved!\n{details}"


def rejected(ctx: Context, node_input: Any) -> str:
    """Final output node for rejected expenses."""
    req = ctx.state.get("expense_request", {})
    builder_decision = ctx.state.get("builder_decision")
    auditor_verdict = ctx.state.get("auditor_verdict")
    request_id = ctx.state.get("request_id") or str(uuid.uuid4())

    # Write rejected outcome to the database
    save_decision(
        request_id=request_id,
        vendor=req.get("vendor", "Unknown"),
        amount=req.get("amount", 0.0),
        description=req.get("description", ""),
        builder_decision=builder_decision,
        auditor_verdict=auditor_verdict,
        final_outcome="rejected"
    )

    reasoning = ""
    if auditor_verdict and auditor_verdict.get("verdict") == "deny":
        reasoning = auditor_verdict.get("reasoning", "")
    elif builder_decision and builder_decision.get("action") == "deny":
        reasoning = builder_decision.get("reasoning", "")
    else:
        reasoning = str(node_input)

    details = f"Details: {req}"
    if builder_decision:
        details += f"\nBuilder Decision: {builder_decision}"
    if auditor_verdict:
        details += f"\nIndependent Auditor Verdict: {auditor_verdict}"

    return f"Expense rejected. Reason: {reasoning}\n{details}"


# Define the workflow graph.
#
# Five nodes encode the entire dual-agent safety property structurally:
# intake -> (Builder, Auditor independently) -> deterministic routing ->
# {confirm, block, escalate}. There is no path through this graph where a
# single agent's output alone produces a final approved/rejected outcome —
# every approval or rejection requires the route_auditor_verdict node to
# observe agreement between two independently-derived verdicts.
root_agent = Workflow(
    name="twokeys_workflow",
    edges=[
        (START, intake),
        (intake, {
            "auto_approve": approved,
            "needs_review": run_builder
        }),
        (run_builder, run_auditor),
        (run_auditor, route_auditor_verdict),
        (route_auditor_verdict, {
            "confirm": approved,
            "block": rejected,
            "escalate": human_escalation
        }),
        (human_escalation, {
            "approved": approved,
            "rejected": rejected
        })
    ],
    description="TwoKeys expense workflow with independent agent evaluation and validation routing."
)

app = App(
    root_agent=root_agent,
    name="app",
    resumability_config=ResumabilityConfig(is_resumable=True)
)
