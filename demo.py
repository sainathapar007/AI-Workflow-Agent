"""Command-line demo of the key scenarios (no UI, no API key needed).

    python demo.py
"""
import os

from agent import config

config.DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo.db")
config.BACKOFF_SECONDS = 0.2
config.SIM_LATENCY = 0.1
if os.path.exists(config.DB_PATH):
    os.remove(config.DB_PATH)

from agent import db, engine, telemetry  # noqa: E402
from agent.samples import SAMPLES  # noqa: E402

RULES = {"planner": "rules"}


def show(title, run):
    print("\n" + "=" * 92)
    print(f"{title}\nRUN #{run['id']}  STATUS: {run['status']}")
    print("-" * 92)
    for s in db.get_steps(run["id"]):
        err = f"  !! {s['error'][:70]}" if s["error"] else ""
        print(f"{s['step_no']:>2}. b{s['batch']} {s['tool']:<22} {s['status']:<18} attempt={s['attempt']} "
              f"@{s['started_ms']:>4}ms +{s['duration_ms']:>3}ms{err}")
    m = telemetry.summary(db.get_steps(run["id"]))
    print(f"-> emails sent: {len(db.list_outbox(run['id']))} | escalations: {len(db.list_escalations(run['id']))} "
          f"| parallelism saved {m['parallel_saved_ms']} ms | retries {m['retries']}")


def main():
    run = engine.start_run(SAMPLES["Double charge (happy path)"], RULES)
    show("1a. SUCCESS: agent pauses before the external action", run)
    print("\nDRAFT TO APPROVE:\n" + run["state"]["draft"])
    show("1b. SUCCESS: human approved -> email sent -> CRM updated", engine.approve(run["id"], reviewer="demo-reviewer"))

    run = engine.start_run(SAMPLES["Double charge (happy path)"], {**RULES, "fail_tool": "send_email", "fail_times": 99})
    show("2. SAFE FAILURE: email outage -> 3 attempts -> FAILED_SAFE + escalation, nothing sent",
         engine.approve(run["id"], reviewer="demo-reviewer"))

    run = engine.start_run(SAMPLES["Double charge (happy path)"], {**RULES, "unsafe_draft": True})
    show("3. SELF-CORRECTION: unsafe first draft is blocked and rewritten", run)

    show("4. PROMPT INJECTION: detected and escalated, card number masked",
         engine.start_run(SAMPLES["Prompt injection attempt (escalate)"], RULES))
    show("5. DEGRADED MODE: CRM lookup is down, agent continues with safe defaults",
         engine.start_run(SAMPLES["App crashing"], {**RULES, "fail_tool": "lookup_customer", "fail_times": 99}))
    run = engine.start_run(SAMPLES["App crashing"], RULES)
    show("6. HUMAN REJECTS: nothing is sent", engine.reject(run["id"], "Wrong tone"))


if __name__ == "__main__":
    main()
