"""Streamlit UI for the AI workflow agent.   Run with:  streamlit run app.py

Tabs: Run the agent | History & analytics | Evaluate | Tools & tech | Problem & design
"""
import json
import time

import pandas as pd
import streamlit as st

from agent import config, db, engine, evals, llm, telemetry, ui
from agent.samples import SAMPLES
from agent.tools import REGISTRY

st.set_page_config(page_title="AI Workflow Agent", page_icon="🤖", layout="wide")
db.init_db()
st.markdown(ui.load_css(), unsafe_allow_html=True)

SAMPLE_ICONS = {"Double charge": "💳", "Password reset": "🔑", "App crashing": "💥", "Possible hack": "🕵️",
                "Legal threat": "⚖️", "Prompt injection": "🧨", "Unknown topic": "❓", "Angry VIP": "🔥", "Invalid email": "📧"}
STEP_COLS = ["step_no", "tool", "status", "attempt", "batch", "planner", "reason", "error", "started_ms", "duration_ms"]


def short_name(n):
    return n.split(" (")[0]


def icon_for(n):
    return next((v for k, v in SAMPLE_ICONS.items() if n.startswith(k)), "🎫")


# ---- one-shot messages (toast / balloons survive st.rerun this way)
if "flash" in st.session_state:
    _icon, _msg = st.session_state.pop("flash")
    st.toast(_msg, icon=_icon)
if st.session_state.pop("balloons", False):
    st.balloons()

# ---- first-visit welcome dialog
if hasattr(st, "dialog") and not st.session_state.get("welcome_seen"):
    st.session_state["welcome_seen"] = True

    @st.dialog("👋 Welcome - how this works")
    def _welcome():
        st.markdown(ui.howto_html(), unsafe_allow_html=True)
        st.caption("Tip: use the ⚡ quick-demo buttons on the first tab. Switch to *Expert* view in the sidebar for traces and audit logs.")
        if st.button("Let's go 🚀", type="primary"):
            st.rerun()
    _welcome()

st.markdown(ui.hero_html(), unsafe_allow_html=True)
st.markdown(ui.kpi_html(db.list_runs(), len(db.list_outbox()), len(db.list_escalations())), unsafe_allow_html=True)

# ================================================================== sidebar
with st.sidebar:
    st.markdown("### ⚙️ Settings")
    view = st.radio("View", ["Simple", "Expert"], horizontal=True,
                    help="Simple = plain-language story. Expert adds the trace waterfall, audit log and JSON export.")
    expert = view == "Expert"
    mode = st.radio("Planner", ["auto", "rules"], horizontal=True,
                    help="auto = LLM planner when an API key is configured, otherwise rules.")
    prov = llm.provider() if llm.available() else None
    st.caption(f"🟢 LLM planner available ({prov})" if prov else "⚪ No API key - using the rule-based planner")
    animate = st.toggle("✨ Animate agent steps", value=True)
    celebrate = st.toggle("🎉 Celebrate successful sends", value=True)
    speed = st.select_slider("Animation speed", ["slow", "normal", "fast"], value="normal")
    delay = {"slow": 0.7, "normal": 0.35, "fast": 0.12}[speed]
    st.divider()
    st.markdown("### 🧹 Demo data")
    sure = st.checkbox("I want to delete all runs")
    if st.button("Reset demo data", disabled=not sure, use_container_width=True):
        db.reset_all()
        st.session_state.pop("run_id", None)
        st.session_state["flash"] = ("🧹", "All demo data deleted")
        st.rerun()


# ================================================================== helpers
def launch(ticket, options=None):
    opts = {"planner": mode, **(options or {})}
    with st.spinner("Agent is working..."):
        run = engine.start_run(ticket, opts)
    st.session_state["run_id"] = run["id"]
    st.session_state["animate"] = run["id"]
    st.rerun()


def play(run_id, pipe_ph, tl_ph, banner_ph=None, pace=0.35):
    """Replay a run step by step so the agent feels live."""
    run, steps = db.get_run(run_id), db.get_steps(run_id)
    degraded = run["state"].get("degraded")
    for i in range(1, len(steps) + 1):
        last = i == len(steps)
        status = run["status"] if last else "RUNNING"
        pipe_ph.markdown(ui.pipeline_html(steps[:i], status), unsafe_allow_html=True)
        tl_ph.markdown(ui.timeline_html(steps[:i]), unsafe_allow_html=True)
        if banner_ph is not None:
            banner_ph.markdown(ui.banner_html(status, degraded if last else None), unsafe_allow_html=True)
        if not last:
            time.sleep(pace)


def approval_panel(run_id, state):
    st.markdown(ui.approval_header_html(state.get("pending_action"), state["ticket"].get("email"), state.get("risk")),
                unsafe_allow_html=True)
    high = (state.get("risk") or {}).get("level") == "high"
    left, right = st.columns([3, 2])
    with left:
        edited = st.text_area("✍️ Draft reply - you can edit it before approving", state.get("draft", ""),
                              height=320, key=f"draft_{run_id}")
        st.caption(f"{len(edited.split())} words · edits are re-checked against the safety policy and logged")
        reviewer = st.text_input("Your name" + (" (required for high-risk replies)" if high else " (optional)"),
                                 key=f"rev_{run_id}")
        confirmed = True
        if high:
            confirmed = st.checkbox("I have read the draft and the risk reasons", key=f"conf_{run_id}")
        note = st.text_input("Note (saved if you reject)", key=f"note_{run_id}")
        b1, b2, _ = st.columns([1.5, 1, 2])
        can_approve = (not high) or (bool(reviewer.strip()) and confirmed)
        if b1.button("✅ Approve & send", type="primary", key=f"ok_{run_id}", use_container_width=True,
                     disabled=not can_approve):
            try:
                result = engine.approve(run_id, edited, reviewer)
            except ValueError as e:
                st.error(str(e))
            else:
                if result["status"] == "COMPLETED":
                    st.session_state["flash"] = ("📧", "Approved - email sent")
                    st.session_state["balloons"] = celebrate
                else:
                    st.session_state["flash"] = ("🛡️", "Send failed safely - nothing sent, ticket escalated")
                st.session_state["animate"] = run_id
                st.rerun()
        if b2.button("❌ Reject", key=f"no_{run_id}", use_container_width=True):
            engine.reject(run_id, note, reviewer)
            st.session_state["flash"] = ("🚫", "Rejected - nothing was sent")
            st.rerun()
        if high and not can_approve:
            st.caption("🔒 High-risk reply: enter your name and tick the box to enable approval.")
    with right:
        st.markdown("**Why you can (or can't) trust this draft**")
        st.markdown(ui.risk_html(state.get("risk")), unsafe_allow_html=True)
        st.markdown(ui.checks_html(state.get("policy")), unsafe_allow_html=True)
        if state.get("articles"):
            with st.expander("📚 Evidence the draft is based on", expanded=False):
                st.markdown(ui.evidence_html(state["articles"]), unsafe_allow_html=True)


def render_run(run_id):
    run = db.get_run(run_id)
    state, steps = run["state"], db.get_steps(run_id)
    animating = animate and st.session_state.pop("animate", None) == run_id
    st.session_state.pop("animate", None)

    st.markdown(f'<div class="section-title">🧠 Run #{run_id} <span class="muted">· {ui._e(run["subject"])}</span></div>',
                unsafe_allow_html=True)
    banner_ph, pipe_ph = st.empty(), st.empty()
    st.markdown(ui.cards_html(state), unsafe_allow_html=True)
    if state["options"].get("fail_tool"):
        st.info(f"🧪 Chaos mode: **{state['options']['fail_tool']}** is set to fail "
                f"{state['options'].get('fail_times', 99)} time(s). Watch how the agent reacts.")
    if state["options"].get("unsafe_draft"):
        st.info("🧪 Chaos mode: the first draft deliberately contains an unsafe promise. The safety check should catch it.")
    approval_box = st.container()

    labels = ["📖 Story", "⏱ Timeline"] + (["🔬 Trace", "🛡️ Trust & audit"] if expert else [])
    tabs = st.tabs(labels)
    with tabs[0]:
        st.markdown(ui.story_html(state, steps, run["status"]), unsafe_allow_html=True)
        if state.get("articles") and run["status"] != "AWAITING_APPROVAL":
            with st.expander("📚 Evidence from the knowledge base"):
                st.markdown(ui.evidence_html(state["articles"]), unsafe_allow_html=True)
        if state.get("risk") and run["status"] != "AWAITING_APPROVAL":
            st.markdown(ui.risk_html(state["risk"]) + ui.checks_html(state.get("policy")), unsafe_allow_html=True)
    with tabs[1]:
        tl_ph = st.empty()
        if animating:
            play(run_id, pipe_ph, tl_ph, banner_ph, delay)
        else:
            banner_ph.markdown(ui.banner_html(run["status"], state.get("degraded")), unsafe_allow_html=True)
            pipe_ph.markdown(ui.pipeline_html(steps, run["status"]), unsafe_allow_html=True)
            tl_ph.markdown(ui.timeline_html(steps), unsafe_allow_html=True)
    if expert:
        with tabs[2]:
            st.markdown("Spans follow the OpenTelemetry GenAI naming: `invoke_agent` → `execute_tool <name>`. "
                        "Bars that overlap ran **in parallel**.")
            st.markdown(ui.trace_summary_html(telemetry.summary(steps)), unsafe_allow_html=True)
            st.markdown(ui.waterfall_html(steps), unsafe_allow_html=True)
            st.download_button("⬇️ Download trace (JSON)", json.dumps(telemetry.export_trace(run_id), indent=2),
                               file_name=f"trace_run_{run_id}.json", mime="application/json")
            st.dataframe(pd.DataFrame(steps)[STEP_COLS], use_container_width=True, hide_index=True)
        with tabs[3]:
            audit = db.list_audit(run_id)
            st.caption("Everything the trust layer did: masking, injection scan, policy results, human decisions.")
            if state.get("masked"):
                c1, c2 = st.columns(2)
                c1.markdown("**Original ticket**")
                c1.code(state["ticket"]["body"], language=None)
                c2.markdown("**What an LLM would see (masked)**")
                c2.code(state["masked"]["body"], language=None)
            st.dataframe(pd.DataFrame([{"time": a["ts"], "event": a["event"],
                                        "details": json.dumps(a["payload"], default=str)[:400]} for a in audit]),
                         use_container_width=True, hide_index=True)

    with approval_box:
        if run["status"] == "AWAITING_APPROVAL":
            approval_panel(run_id, state)
    if run["status"] == "COMPLETED":
        mails = db.list_outbox(run_id)
        if mails:
            with st.expander("📧 Email that was sent", expanded=True):
                st.write(f"**To:** {mails[0]['to_email']}  \n**Subject:** {mails[0]['subject']}")
                st.code(mails[0]["body"], language=None)


# ================================================================== tabs
tab_run, tab_hist, tab_eval, tab_tools, tab_about = st.tabs(
    ["▶ Run the agent", "🗂 History & analytics", "🧪 Evaluate", "🧰 Tools & tech", "📘 Problem & design"])

# ------------------------------------------------------------------ RUN
with tab_run:
    cur_status = db.get_run(st.session_state["run_id"])["status"] if "run_id" in st.session_state else None
    st.markdown(ui.wizard_html(cur_status), unsafe_allow_html=True)

    st.markdown('<div class="section-title" style="margin-top:4px">⚡ Quick demos <span class="muted">· one click</span></div>',
                unsafe_allow_html=True)
    demos = [
        ("✅ Success", "Double charge (happy path)", {}, "Reads, drafts, pauses for your approval, sends."),
        ("🛡️ Safe failure", "Double charge (happy path)", {"fail_tool": "send_email", "fail_times": 99},
         "Email service is down: retries, then stops safely and escalates."),
        ("🔁 Self-correction", "Double charge (happy path)", {"unsafe_draft": True},
         "First draft breaks policy; the agent rewrites it."),
        ("🧨 Injection", "Prompt injection attempt (escalate)", {}, "Hostile ticket is detected and escalated."),
        ("🔥 High risk", "Angry VIP refund (high risk)", {}, "Angry VIP: approval needs a named reviewer."),
    ]
    for col, (label, sample, opts, tip) in zip(st.columns(len(demos)), demos):
        with col:
            if st.button(label, key=f"demo_{label}", use_container_width=True, help=tip):
                launch(SAMPLES[sample], opts)
            st.caption(tip)

    with st.expander("✍️ Compose your own ticket", expanded="run_id" not in st.session_state):
        names = list(SAMPLES)
        st.session_state.setdefault("sample", names[0])
        labels = {n: f"{icon_for(n)} {short_name(n)}" for n in names}
        if hasattr(st, "pills"):
            picked = st.pills("Scenario", names, format_func=lambda n: labels[n], selection_mode="single",
                              default=st.session_state["sample"], key="sample_pills")
            if picked:
                st.session_state["sample"] = picked
        else:
            st.session_state["sample"] = st.selectbox("Scenario", names, format_func=lambda n: labels[n])
        sample = st.session_state["sample"]
        t = SAMPLES[sample]
        c1, c2 = st.columns(2)
        name = c1.text_input("Customer name", t["name"], key=f"n_{sample}")
        email = c2.text_input("Customer email", t["email"], key=f"e_{sample}")
        subject = st.text_input("Subject", t["subject"], key=f"s_{sample}")
        body = st.text_area("Message", t["body"], key=f"b_{sample}", height=110)
        with st.popover("🧪 Chaos mode (simulate problems)"):
            fail_tool = st.selectbox("Force an outage in", ["None"] + list(REGISTRY))
            fail_times = st.number_input("Failed attempts before recovery", 1, 9, 3,
                                         help="Each tool gets 3 attempts: 1-2 recovers after retries, 3+ fails.")
            unsafe = st.checkbox("First draft contains an unsafe promise")
        if st.button("🚀 Run agent", type="primary"):
            options = {}
            if fail_tool != "None":
                options.update({"fail_tool": fail_tool, "fail_times": int(fail_times)})
            if unsafe:
                options["unsafe_draft"] = True
            launch({"name": name, "email": email, "subject": subject, "body": body}, options)

    if "run_id" in st.session_state:
        st.divider()
        render_run(st.session_state["run_id"])

# ------------------------------------------------------------------ HISTORY
with tab_hist:
    runs = db.list_runs()
    if not runs:
        st.info("No runs yet - click a quick demo on the first tab.")
    else:
        left, right = st.columns([3, 2])
        with left:
            st.markdown('<div class="section-title">📋 All runs</div>', unsafe_allow_html=True)
            st.dataframe(pd.DataFrame(runs), use_container_width=True, hide_index=True)
        with right:
            st.markdown('<div class="section-title">📊 Outcomes</div>', unsafe_allow_html=True)
            st.bar_chart(pd.DataFrame(runs)["status"].value_counts())
        st.markdown('<div class="section-title">🔍 Inspect & replay a run</div>', unsafe_allow_html=True)
        pick = st.selectbox("Run", [r["id"] for r in runs],
                            format_func=lambda i: f"#{i} - " + next(r["subject"] for r in runs if r["id"] == i))
        pr, psteps = db.get_run(pick), db.get_steps(pick)
        banner_ph, pipe_ph = st.empty(), st.empty()
        st.markdown(ui.trace_summary_html(telemetry.summary(psteps)), unsafe_allow_html=True)
        tl_ph = st.empty()
        banner_ph.markdown(ui.banner_html(pr["status"], pr["state"].get("degraded")), unsafe_allow_html=True)
        pipe_ph.markdown(ui.pipeline_html(psteps, pr["status"]), unsafe_allow_html=True)
        tl_ph.markdown(ui.timeline_html(psteps), unsafe_allow_html=True)
        if st.button("▶ Replay this run"):
            play(pick, pipe_ph, tl_ph, banner_ph, delay)
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown('<div class="section-title">📤 Outbox</div>', unsafe_allow_html=True)
        out = db.list_outbox()
        st.dataframe(pd.DataFrame(out) if out else pd.DataFrame(columns=["run_id", "to_email", "subject"]),
                     use_container_width=True, hide_index=True)
    with c2:
        st.markdown('<div class="section-title">🚨 Escalations</div>', unsafe_allow_html=True)
        esc = db.list_escalations()
        st.dataframe(pd.DataFrame(esc) if esc else pd.DataFrame(columns=["run_id", "queue", "reason"]),
                     use_container_width=True, hide_index=True)
    with c3:
        st.markdown('<div class="section-title">🗂️ CRM updates</div>', unsafe_allow_html=True)
        crm = db.list_crm()
        st.dataframe(pd.DataFrame(crm) if crm else pd.DataFrame(columns=["run_id", "status", "category"]),
                     use_container_width=True, hide_index=True)

# ------------------------------------------------------------------ EVALUATE
with tab_eval:
    st.markdown("Agents are non-deterministic systems with side effects, so we **measure** them. The suite runs "
                f"{len(evals.CASES)} core and {len(evals.HARD_CASES)} hard labelled tickets through the full agent "
                "on a throw-away database (your demo data is untouched).")
    st.caption("Honest note: the *core* set was used while building the agent, so treat it as a regression test. "
               "The *hard* set (typos, Hinglish, subtle injection) shows real limitations.")
    if st.button("▶ Run evaluation suite", type="primary"):
        with st.spinner("Running labelled tickets..."):
            st.session_state["eval"] = evals.run_suite()
    res = st.session_state.get("eval")
    if res:
        for g, title in (("core", "Core set (regression)"), ("hard", "Hard set (stress test)")):
            if g in res["by_group"]:
                st.markdown(ui.eval_cards_html(res["by_group"][g], title), unsafe_allow_html=True)
        df = pd.DataFrame(res["rows"])
        df["result"] = ["✅" if (a and b) else "❌" for a, b in zip(df["category_ok"], df["outcome_ok"])]
        st.dataframe(df[["result", "id", "group", "case", "expected_category", "got_category", "expected", "got", "risk", "tool_calls", "ms"]],
                     use_container_width=True, hide_index=True)
        st.info("Failures on the hard set are mostly **safe** (the agent escalates to a human instead of guessing): "
                "escalation recall stays at 100%, while automation rate drops. That is the right failure direction.")

# ------------------------------------------------------------------ TOOLS
with tab_tools:
    st.markdown('<div class="section-title">🧰 Tools the planner can choose from</div>', unsafe_allow_html=True)
    st.markdown(ui.tools_html(REGISTRY), unsafe_allow_html=True)
    st.markdown('<div class="section-title">🛡️ How the agent stays safe</div>', unsafe_allow_html=True)
    st.markdown(ui.safety_html(), unsafe_allow_html=True)
    st.markdown('<div class="section-title">🔬 Technology choices (researched)</div>', unsafe_allow_html=True)
    st.markdown(ui.tech_html(), unsafe_allow_html=True)
    st.markdown('<div class="section-title">🔌 Use the agent from any MCP client</div>', unsafe_allow_html=True)
    st.markdown("Run `python mcp_server.py` and add it to an MCP client (for example Claude Desktop). Clients can classify, "
                "search, mask PII and **submit** tickets - but approval and sending stay human-only in this UI.")
    st.code(json.dumps({"mcpServers": {"support-agent": {"command": "python", "args": [str(config.BASE_DIR / "mcp_server.py")]}}}, indent=2),
            language="json")
    with st.expander("📚 Knowledge base the agent searches"):
        kb = json.load(open(config.KB_PATH, encoding="utf-8"))
        st.dataframe(pd.DataFrame([{"id": a["id"], "category": a["category"], "title": a["title"]} for a in kb]),
                     use_container_width=True, hide_index=True)

# ------------------------------------------------------------------ ABOUT
with tab_about:
    st.markdown('<div class="section-title">📘 Understanding the problem</div>', unsafe_allow_html=True)
    st.markdown(ui.overview_html(), unsafe_allow_html=True)
    st.markdown("""
| Deliverable | Where it lives |
|---|---|
| Task-planning and tool-execution engine | `agent/planner.py`, `agent/engine.py` |
| At least three working tools | `agent/tools.py` - 11 tools (read-only, internal-write and external) |
| Execution history | SQLite `steps` + `audit`, timeline / trace / audit views |
| Error handling | retries, schema validation, graceful degradation, fail-safe escalation, budgets |
| Human-approval step | `send_email` is `requires_approval`; risk-based reviewer rules |
| Demo: success + safe failure | quick-demo buttons, `python demo.py` |
""")
    st.caption("Full write-up: docs/PROBLEM_UNDERSTANDING.md")
