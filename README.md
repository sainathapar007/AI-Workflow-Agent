# AI Agent That Completes Workflows (v2)

An agent that handles a support ticket end to end - **understand -> retrieve -> draft -> safety check -> human approval ->
send** - choosing from 11 tools, with parallel execution, a trust layer, full tracing, and evals.
See `docs/PROBLEM_UNDERSTANDING.md` for the problem analysis, design decisions and sources.

## Quick start
```bash
pip install -r requirements.txt
streamlit run app.py              # the UI (click a ⚡ quick demo)
python demo.py                    # CLI demo of 6 scenarios
python -m unittest discover -s tests -v   # 32 tests
python mcp_server.py              # optional: expose the agent over MCP (stdio)
```
Optional LLM planner/drafts: set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` (and `LLM_PROVIDER`, `LLM_MODEL`, `OPENAI_MODEL`).
Without a key everything runs on the rule-based planner.

## What's new in v2
| Area | Feature |
|---|---|
| Engine | Parallel execution of independent read-only tools; JSON-Schema argument validation; budgets; graceful degradation; resumable approval |
| Tools (11) | lookup_customer, analyze_sentiment, redact_pii, classify_ticket, triage_ticket, search_knowledge_base (BM25), draft_reply, policy_check, send_email, update_crm, escalate_to_human |
| Safety | PII masking + de-masking, prompt-injection detection, toxicity/policy guardrail, reflection loop, risk score, named reviewer for high risk, re-check of human edits |
| Observability | Spans (batch/start/duration), waterfall view, OTel-GenAI-style JSON export, audit trail |
| Evals | 16 core + 7 hard labelled tickets, escalation precision/recall, safety pass |
| Integrations | Claude + OpenAI native tool-calling planner, MCP server |
| UI | Simple/Expert views, wizard, quick demos, live replay, story view, risk meter, evidence, trace, audit, eval tab |

## Demo script
1. **Success** - click *✅ Success*: parallel understanding, triage, retrieval, draft, safety check, pause. Approve -> email + CRM.
2. **Safe failure** - *🛡️ Safe failure*, approve: 3 attempts fail -> `FAILED_SAFE`, nothing sent, escalated.
3. **Self-correction** - *🔁*: unsafe draft blocked, rewritten.
4. **Injection** - *🧨*: detected, card masked, escalated to Security.
5. **High risk** - *🔥*: approval requires a named reviewer.
6. **Evaluate** tab -> run the suite.

## Project layout
```
app.py  demo.py  mcp_server.py
agent/ engine.py planner.py tools.py trust.py retrieval.py schema.py llm.py telemetry.py evals.py db.py ui.py samples.py config.py
assets/style.css   .streamlit/config.toml   data/kb.json customers.json   docs/   tests/
```
Edit colours at the top of `assets/style.css`. Add a tool with `@register(...)` in `agent/tools.py`.
