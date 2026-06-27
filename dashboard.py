"""
TwoKeys Dashboard — Expense Approval Intelligence
Real-time observability into dual-agent decision making.
"""

import streamlit as st
import sqlite3
import json
import os
import pandas as pd
from datetime import datetime

st.set_page_config(
    page_title="TwoKeys — Expense Intelligence",
    page_icon="🔐",
    layout="wide",
    initial_sidebar_state="collapsed"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=Inter:wght@300;400;500;600&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
    background-color: #080b12;
    color: #e2e8f0;
}
#MainMenu, footer, header { visibility: hidden; }
.block-container { padding: 2rem 2.5rem; max-width: 1400px; }

/* ── Hero ── */
.hero {
    display: flex;
    justify-content: space-between;
    align-items: flex-end;
    border-bottom: 1px solid #1a2035;
    padding-bottom: 1.25rem;
    margin-bottom: 1.75rem;
}
.hero-left {}
.hero-title {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 2rem;
    font-weight: 600;
    color: #f8fafc;
    letter-spacing: -0.03em;
    margin: 0;
    line-height: 1;
}
.hero-title span { color: #818cf8; }
.hero-sub {
    font-size: 0.72rem;
    color: #475569;
    margin-top: 0.4rem;
    letter-spacing: 0.1em;
    text-transform: uppercase;
}
.hero-badge {
    display: flex;
    align-items: center;
    gap: 6px;
    background: #0f1629;
    border: 1px solid #1e293b;
    border-radius: 6px;
    padding: 0.4rem 0.8rem;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.7rem;
    color: #10b981;
}
.live-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: #10b981;
    animation: pulse 2s infinite;
}
@keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
}

/* ── Hero stat (intercepted) ── */
.hero-stat {
    text-align: right;
}
.hero-stat-value {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 2.4rem;
    font-weight: 600;
    color: #f87171;
    line-height: 1;
    letter-spacing: -0.03em;
}
.hero-stat-label {
    font-size: 0.68rem;
    color: #475569;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-top: 0.3rem;
}
.hero-stat-sub {
    font-size: 0.7rem;
    color: #334155;
    margin-top: 0.15rem;
    font-family: 'IBM Plex Mono', monospace;
}

/* ── Metric cards ── */
.metric-grid {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 10px;
    margin-bottom: 1.75rem;
}
.mcard {
    background: #0d1117;
    border: 1px solid #1a2035;
    border-radius: 8px;
    padding: 1.1rem 1.25rem;
    position: relative;
    overflow: hidden;
}
.mcard::after {
    content: '';
    position: absolute;
    bottom: 0; left: 0; right: 0;
    height: 1px;
    opacity: 0.3;
}
.mcard-accent { border-left: 2px solid #818cf8; }
.mcard-green  { border-left: 2px solid #10b981; }
.mcard-red    { border-left: 2px solid #ef4444; }
.mcard-amber  { border-left: 2px solid #f59e0b; }

.mcard-icon {
    font-size: 1.1rem;
    margin-bottom: 0.5rem;
    opacity: 0.6;
}
.mcard-value {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 2rem;
    font-weight: 600;
    color: #f1f5f9;
    line-height: 1;
}
.mcard-label {
    font-size: 0.68rem;
    color: #475569;
    text-transform: uppercase;
    letter-spacing: 0.09em;
    margin-top: 0.35rem;
}
.mcard-delta {
    font-size: 0.7rem;
    color: #334155;
    margin-top: 0.2rem;
    font-family: 'IBM Plex Mono', monospace;
}

/* ── Section headers ── */
.sec-hdr {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.62rem;
    color: #334155;
    text-transform: uppercase;
    letter-spacing: 0.15em;
    border-bottom: 1px solid #1a2035;
    padding-bottom: 0.45rem;
    margin-bottom: 0.9rem;
}

/* ── Decision cards ── */
.dcard {
    background: #0d1117;
    border: 1px solid #1a2035;
    border-radius: 8px;
    padding: 1rem 1.1rem 1rem 0;
    margin-bottom: 0.65rem;
    display: flex;
    gap: 0;
    overflow: hidden;
}
.dcard-stripe {
    width: 3px;
    min-height: 100%;
    flex-shrink: 0;
    margin-right: 1rem;
    border-radius: 0;
}
.dcard-stripe-approved { background: #10b981; }
.dcard-stripe-rejected { background: #ef4444; }
.dcard-stripe-escalated { background: #f59e0b; }
.dcard-stripe-unknown { background: #334155; }

.dcard-body { flex: 1; }
.dcard-head {
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    margin-bottom: 0.65rem;
}
.dcard-vendor { font-weight: 600; font-size: 0.92rem; color: #f1f5f9; }
.dcard-amount {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.78rem;
    color: #64748b;
    margin-top: 0.15rem;
}
.dcard-desc { font-size: 0.75rem; color: #475569; margin-top: 0.1rem; }
.dcard-ts {
    font-size: 0.65rem;
    color: #334155;
    font-family: 'IBM Plex Mono', monospace;
    text-align: right;
}

/* ── Badges ── */
.badge {
    display: inline-block;
    padding: 0.18rem 0.55rem;
    border-radius: 4px;
    font-size: 0.63rem;
    font-weight: 600;
    font-family: 'IBM Plex Mono', monospace;
    text-transform: uppercase;
    letter-spacing: 0.06em;
}
.badge-approved  { background: #052e16; color: #34d399; border: 1px solid #064e3b; }
.badge-rejected  { background: #1c0a0a; color: #f87171; border: 1px solid #7f1d1d; }
.badge-escalated { background: #1c1506; color: #fbbf24; border: 1px solid #78350f; }
.badge-unknown   { background: #111827; color: #6b7280; border: 1px solid #374151; }

/* ── Agent boxes ── */
.agent-row {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.6rem;
}
.abox {
    background: #070b14;
    border: 1px solid #1a2035;
    border-radius: 6px;
    padding: 0.65rem 0.75rem;
}
.alabel {
    font-size: 0.6rem;
    font-family: 'IBM Plex Mono', monospace;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    margin-bottom: 0.3rem;
    display: flex;
    align-items: center;
    gap: 5px;
}
.alabel-dot {
    width: 5px; height: 5px;
    border-radius: 50%;
    flex-shrink: 0;
}
.alabel-builder { color: #818cf8; }
.alabel-builder .alabel-dot { background: #818cf8; }
.alabel-auditor { color: #a78bfa; }
.alabel-auditor .alabel-dot { background: #a78bfa; }
.areason { font-size: 0.73rem; color: #64748b; line-height: 1.5; }

/* ── Bar charts ── */
.bar-row { margin-bottom: 0.7rem; }
.bar-top {
    display: flex;
    justify-content: space-between;
    font-size: 0.75rem;
    color: #94a3b8;
    margin-bottom: 0.25rem;
}
.bar-mono { font-family: 'IBM Plex Mono', monospace; }
.bar-bg { background: #0d1117; border-radius: 2px; height: 3px; border: 1px solid #1a2035; }

/* ── Vendor risk ── */
.vrow {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0.55rem 0;
    border-bottom: 1px solid #0d1117;
}
.vname2 { font-size: 0.82rem; color: #cbd5e1; font-weight: 500; }
.vmeta { font-size: 0.65rem; color: #475569; margin-top: 0.1rem; }
.vrisk-bar { width: 64px; }

/* ── Escalations ── */
.esc-row {
    padding: 0.55rem 0;
    border-bottom: 1px solid #0d1117;
}
.esc-vendor { font-size: 0.82rem; color: #fbbf24; font-weight: 500; }
.esc-meta { font-size: 0.65rem; color: #475569; margin-top: 0.1rem; font-family: 'IBM Plex Mono', monospace; }
.esc-desc { font-size: 0.72rem; color: #334155; margin-top: 0.2rem; line-height: 1.4; }

/* ── Architecture callout ── */
.arch-box {
    background: #0d1117;
    border: 1px solid #232b45;
    border-radius: 8px;
    padding: 1rem 1.25rem;
    margin-top: 1rem;
}
.arch-flow {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    flex-wrap: wrap;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.72rem;
    color: #475569;
}
.arch-node {
    background: #0d1117;
    border: 1px solid #1e293b;
    border-radius: 4px;
    padding: 0.25rem 0.6rem;
    color: #94a3b8;
}
.arch-node.key { border-color: #818cf8; color: #818cf8; }
.arch-arrow { color: #334155; }

/* ── Divider ── */
.div { border: none; border-top: 1px solid #1a2035; margin: 1rem 0; }

/* ── Empty state ── */
.empty {
    text-align: center;
    padding: 2rem;
    color: #1e293b;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.8rem;
}

/* ── Footer ── */
.footer {
    display: flex;
    justify-content: space-between;
    padding: 0.75rem 0 0;
    border-top: 1px solid #0d1117;
    font-family: 'IBM Plex Mono', monospace;
    font-size: 0.62rem;
    color: #1e293b;
    margin-top: 1.5rem;
}
</style>
""", unsafe_allow_html=True)

# ── Database ────────────────────────────────────────────────────────────────
DB_PATH = os.environ.get("TWOKEYS_DB_PATH", "twokeys_memory.db")

@st.cache_data(ttl=5)
def load_decisions():
    if not os.path.exists(DB_PATH):
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    try:
        df = pd.read_sql_query(
            "SELECT * FROM expense_decisions ORDER BY timestamp DESC", conn
        )
        conn.close()
        return df
    except Exception:
        conn.close()
        return pd.DataFrame()

def parse_json(val):
    if not val:
        return {}
    try:
        return json.loads(val)
    except Exception:
        return {}

def badge(outcome):
    cls = {"approved": "badge-approved", "rejected": "badge-rejected",
           "escalated": "badge-escalated"}.get(outcome, "badge-unknown")
    sym = {"approved": "✓", "rejected": "✕", "escalated": "⚡"}.get(outcome, "·")
    return f'<span class="badge {cls}">{sym} {outcome}</span>'

def stripe_class(outcome):
    return f"dcard-stripe-{outcome}" if outcome in ["approved","rejected","escalated"] else "dcard-stripe-unknown"

def fmt_amt(v):
    try: return f"${float(v):,.2f}"
    except: return str(v)

def fmt_ts(ts):
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt.strftime("%b %d, %H:%M")
    except: return str(ts)[:16]

# ── Load ────────────────────────────────────────────────────────────────────
df = load_decisions()

total = approved = rejected = escalated = caught_amount = total_amount = 0
if not df.empty:
    total      = len(df)
    approved   = len(df[df.final_outcome == "approved"])
    rejected   = len(df[df.final_outcome == "rejected"])
    escalated  = len(df[df.final_outcome == "escalated"])
    total_amount  = df.amount.sum()
    caught_amount = df[df.final_outcome.isin(["rejected","escalated"])].amount.sum()

# ── Hero ────────────────────────────────────────────────────────────────────
st.markdown(f"""
<div class="hero">
  <div class="hero-left">
    <div class="hero-title">Two<span>Keys</span></div>
    <div class="hero-sub">Dual-agent expense approval intelligence · Kaggle 5-Day AI Agent Capstone</div>
  </div>
  <div style="display:flex;gap:1.5rem;align-items:flex-end">
    <div class="hero-stat">
      <div class="hero-stat-value">{fmt_amt(caught_amount)}</div>
      <div class="hero-stat-label">Total value intercepted</div>
      <div class="hero-stat-sub">{rejected + escalated} of {total} requests blocked or escalated</div>
    </div>
    <div class="hero-badge">
      <div class="live-dot"></div>
      SYSTEM ACTIVE
    </div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── Metric cards ─────────────────────────────────────────────────────────────
approve_rate = f"{approved/total*100:.0f}%" if total > 0 else "—"
st.markdown(f"""
<div class="metric-grid">
  <div class="mcard mcard-accent">
    <div class="mcard-value">{total}</div>
    <div class="mcard-label">Requests processed</div>
    <div class="mcard-delta">${total_amount:,.0f} total volume</div>
  </div>
  <div class="mcard mcard-green">
    <div class="mcard-value">{approved}</div>
    <div class="mcard-label">Approved</div>
    <div class="mcard-delta">{approve_rate} approval rate</div>
  </div>
  <div class="mcard mcard-red">
    <div class="mcard-value">{rejected}</div>
    <div class="mcard-label">Rejected</div>
    <div class="mcard-delta">policy violations caught</div>
  </div>
  <div class="mcard mcard-amber">
    <div class="mcard-value">{escalated}</div>
    <div class="mcard-label">Escalated to human</div>
    <div class="mcard-delta">agent disagreements + failures</div>
  </div>
</div>
""", unsafe_allow_html=True)

# ── Main columns ──────────────────────────────────────────────────────────────
left, right = st.columns([3, 2], gap="large")

# ── Decision feed ─────────────────────────────────────────────────────────────
with left:
    st.markdown('<div class="sec-hdr">Live decision feed — Builder · Auditor reasoning</div>', unsafe_allow_html=True)

    fc1, fc2 = st.columns([2, 1])
    with fc1:
        search = st.text_input("", placeholder="Search vendor or description...", label_visibility="collapsed")
    with fc2:
        f_out = st.selectbox("", ["All outcomes","Approved","Rejected","Escalated"], label_visibility="collapsed")

    if df.empty:
        st.markdown('<div class="empty">🔐 No decisions yet.<br>Submit an expense to the ADK playground to populate the feed.</div>', unsafe_allow_html=True)
    else:
        fdf = df.copy()
        if search:
            mask = (fdf.vendor.str.contains(search, case=False, na=False) |
                    fdf.description.str.contains(search, case=False, na=False))
            fdf = fdf[mask]
        if f_out != "All outcomes":
            fdf = fdf[fdf.final_outcome == f_out.lower()]

        if fdf.empty:
            st.markdown('<div class="empty">No matching decisions.</div>', unsafe_allow_html=True)
        else:
            for _, row in fdf.head(15).iterrows():
                b = parse_json(row.get("builder_decision"))
                a = parse_json(row.get("auditor_verdict"))
                b_action   = b.get("action", "—").upper()
                b_reason   = b.get("reasoning", "Agent did not run — fail-safe escalation triggered.")
                a_verdict  = a.get("verdict", "—").upper()
                a_reason   = a.get("reasoning", "Agent did not run — fail-safe escalation triggered.")
                desc       = str(row.description)
                desc_short = desc[:65] + "…" if len(desc) > 65 else desc
                outcome    = str(row.final_outcome)

                st.markdown(f"""
                <div class="dcard">
                  <div class="dcard-stripe {stripe_class(outcome)}"></div>
                  <div class="dcard-body">
                    <div class="dcard-head">
                      <div>
                        <div class="dcard-vendor">{row.vendor}</div>
                        <div class="dcard-amount">{fmt_amt(row.amount)}</div>
                        <div class="dcard-desc">{desc_short}</div>
                      </div>
                      <div>
                        {badge(outcome)}
                        <div class="dcard-ts" style="margin-top:0.35rem">{fmt_ts(str(row.timestamp))}</div>
                      </div>
                    </div>
                    <div class="agent-row">
                      <div class="abox">
                        <div class="alabel alabel-builder">
                          <div class="alabel-dot"></div>Builder &nbsp;·&nbsp; {b_action}
                        </div>
                        <div class="areason">{b_reason[:200]}{"…" if len(b_reason)>200 else ""}</div>
                      </div>
                      <div class="abox">
                        <div class="alabel alabel-auditor">
                          <div class="alabel-dot"></div>Auditor &nbsp;·&nbsp; {a_verdict}
                        </div>
                        <div class="areason">{a_reason[:200]}{"…" if len(a_reason)>200 else ""}</div>
                      </div>
                    </div>
                  </div>
                </div>
                """, unsafe_allow_html=True)

# ── Right panel ───────────────────────────────────────────────────────────────
with right:

    # Outcome bars
    st.markdown('<div class="sec-hdr">Outcome breakdown</div>', unsafe_allow_html=True)
    if total > 0:
        for label, count, pct, color in [
            ("Approved",  approved,  approved/total*100,  "#10b981"),
            ("Rejected",  rejected,  rejected/total*100,  "#ef4444"),
            ("Escalated", escalated, escalated/total*100, "#f59e0b"),
        ]:
            st.markdown(f"""
            <div class="bar-row">
              <div class="bar-top">
                <span>{label}</span>
                <span class="bar-mono" style="color:{color}">{count} &nbsp;({pct:.0f}%)</span>
              </div>
              <div class="bar-bg">
                <div style="background:{color};width:{pct}%;height:3px;border-radius:2px"></div>
              </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty" style="padding:1rem">No data</div>', unsafe_allow_html=True)

    st.markdown('<hr class="div">', unsafe_allow_html=True)

    # Vendor risk
    st.markdown('<div class="sec-hdr">Vendor risk registry</div>', unsafe_allow_html=True)
    if not df.empty:
        vs = df.groupby("vendor").agg(
            total=("request_id","count"),
            rej=("final_outcome", lambda x: (x=="rejected").sum()),
            esc=("final_outcome", lambda x: (x=="escalated").sum()),
        ).reset_index()
        vs["score"] = vs.rej + vs.esc * 0.5
        vs = vs.sort_values("score", ascending=False)
        max_score = vs.score.max() if vs.score.max() > 0 else 1

        for _, v in vs.head(7).iterrows():
            pct = min(int(v.score / max_score * 100), 100)
            color = "#ef4444" if v.score >= 2 else "#f59e0b" if v.score >= 1 else "#10b981"
            vn = str(v.vendor)[:24] + ("…" if len(str(v.vendor)) > 24 else "")
            st.markdown(f"""
            <div class="vrow">
              <div>
                <div class="vname2">{vn}</div>
                <div class="vmeta">{int(v.total)} requests · {int(v.rej)} rejected · {int(v.esc)} escalated</div>
              </div>
              <div class="vrisk-bar">
                <div class="bar-bg">
                  <div style="background:{color};width:{pct}%;height:3px;border-radius:2px"></div>
                </div>
              </div>
            </div>
            """, unsafe_allow_html=True)
    else:
        st.markdown('<div class="empty" style="padding:1rem">No vendor data</div>', unsafe_allow_html=True)

    st.markdown('<hr class="div">', unsafe_allow_html=True)

    # Recent escalations
    st.markdown('<div class="sec-hdr">Recent escalations</div>', unsafe_allow_html=True)
    if not df.empty:
        esc_df = df[df.final_outcome == "escalated"].head(4)
        if esc_df.empty:
            st.markdown('<div style="font-size:0.78rem;color:#1e293b;padding:0.5rem 0">No escalations recorded</div>', unsafe_allow_html=True)
        else:
            for _, row in esc_df.iterrows():
                desc = str(row.description)
                st.markdown(f"""
                <div class="esc-row">
                  <div class="esc-vendor">{row.vendor}</div>
                  <div class="esc-meta">{fmt_amt(row.amount)} · {fmt_ts(str(row.timestamp))}</div>
                  <div class="esc-desc">{desc[:90]}{"…" if len(desc)>90 else ""}</div>
                </div>
                """, unsafe_allow_html=True)
    else:
        st.markdown('<div style="font-size:0.78rem;color:#1e293b;padding:0.5rem 0">No data</div>', unsafe_allow_html=True)

    st.markdown('<hr class="div">', unsafe_allow_html=True)

    # Architecture flow
    st.markdown('<div class="sec-hdr">Agent architecture</div>', unsafe_allow_html=True)
    st.markdown("""
    <div class="arch-box">
      <div class="arch-flow">
        <span class="arch-node">intake</span>
        <span class="arch-arrow">→</span>
        <span class="arch-node key">builder</span>
        <span class="arch-arrow">→</span>
        <span class="arch-node key">auditor</span>
        <span class="arch-arrow">→</span>
        <span class="arch-node">route</span>
        <span class="arch-arrow">→</span>
        <span class="arch-node">outcome</span>
      </div>
      <div style="font-size:0.65rem;color:#334155;margin-top:0.6rem;line-height:1.6">
        Two independent LLM agents evaluate each request without seeing each other's reasoning.
        Disagreement → automatic human escalation. Agent failure → fail-safe escalation. Never silent.
      </div>
    </div>
    """, unsafe_allow_html=True)

# ── Footer ────────────────────────────────────────────────────────────────────
col1, col2 = st.columns([3, 1])
with col1:
    st.markdown("""
    <div class="footer">
      <span>TWOKEYS · DUAL-AGENT EXPENSE INTELLIGENCE · KAGGLE 5-DAY AI AGENT CAPSTONE 2026</span>
      <span>Google ADK 2.0 · Gemini 2.5 Flash · Antigravity IDE</span>
    </div>
    """, unsafe_allow_html=True)
with col2:
    if st.button("↺  Refresh feed", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
