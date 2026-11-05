"""Expose the agent as a Model Context Protocol (MCP) server over stdio (newline-delimited JSON-RPC 2.0).

    python mcp_server.py

Any MCP client (Claude Desktop, IDEs, other agents) can then use the agent's SAFE tools and file tickets.
Design choice: the approval gate stays human-only. MCP clients can read, classify, search, mask PII and
SUBMIT a ticket (the run stops at 'awaiting approval') - they can NOT approve or send emails.

Rules from the MCP spec that matter here: stdout carries protocol messages only (logs go to stderr),
the server answers `initialize` with a protocol version it supports, and notifications get no response.
"""
import json
import sys
from types import SimpleNamespace

from agent import config, db, engine, schema
from agent.tools import REGISTRY

SERVER_INFO = {"name": "support-workflow-agent", "version": "1.0.0"}
KNOWN_VERSIONS = ["2024-11-05", "2025-03-26", "2025-06-18"]
EXPOSED_TOOLS = ["classify_ticket", "lookup_customer", "analyze_sentiment", "redact_pii", "search_knowledge_base"]

TICKET_PROPS = {"name": {"type": "string"}, "email": {"type": "string"},
                "subject": {"type": "string", "minLength": 1}, "body": {"type": "string", "minLength": 1}}
EXTRA_TOOLS = {
    "submit_ticket": {
        "description": "File a support ticket. The agent runs until it needs human approval, then stops. "
                       "Returns the run id, status, category, risk and the draft reply. It does NOT send anything.",
        "inputSchema": schema.obj(TICKET_PROPS)},
    "get_run": {
        "description": "Get the status and steps of a previous run.",
        "inputSchema": schema.obj({"run_id": {"type": "integer", "minimum": 1}})},
}


def list_tools():
    tools = [{"name": n, "description": REGISTRY[n].description, "inputSchema": REGISTRY[n].parameters}
             for n in EXPOSED_TOOLS]
    tools += [{"name": n, **spec} for n, spec in EXTRA_TOOLS.items()]
    return tools


def call_tool(name, arguments):
    """Returns (payload_dict, is_error)."""
    arguments = arguments or {}
    if name in EXPOSED_TOOLS:
        tool = REGISTRY[name]
        errs = schema.validate(arguments, tool.parameters)
        if errs:
            return {"error": "invalid arguments: " + "; ".join(errs)}, True
        res = tool.fn(arguments, SimpleNamespace(state={"options": {}}, run_id=0))
        return {"summary": res["summary"], "result": res["updates"]}, False
    if name == "submit_ticket":
        errs = schema.validate(arguments, EXTRA_TOOLS[name]["inputSchema"])
        if errs:
            return {"error": "invalid arguments: " + "; ".join(errs)}, True
        run = engine.start_run(arguments, {"planner": "auto"})
        st = run["state"]
        return {"run_id": run["id"], "status": run["status"], "category": st.get("category"),
                "priority": st.get("priority"), "risk": st.get("risk"), "draft": st.get("draft"),
                "note": "A human must approve in the web UI before anything is sent."}, False
    if name == "get_run":
        errs = schema.validate(arguments, EXTRA_TOOLS[name]["inputSchema"])
        if errs:
            return {"error": "invalid arguments: " + "; ".join(errs)}, True
        try:
            run = db.get_run(arguments["run_id"])
        except KeyError:
            return {"error": f"run {arguments['run_id']} not found"}, True
        return {"run_id": run["id"], "status": run["status"],
                "steps": [{"tool": s["tool"], "status": s["status"]} for s in db.get_steps(run["id"])]}, False
    raise KeyError(name)


def handle(msg):
    """Handle one JSON-RPC message; return a response dict, or None for notifications."""
    mid, method, params = msg.get("id"), msg.get("method"), msg.get("params") or {}
    if mid is None:                                   # notification (e.g. notifications/initialized)
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in KNOWN_VERSIONS else KNOWN_VERSIONS[-1]
        return ok({"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": SERVER_INFO,
                   "instructions": "Safe support-ticket tools. Sending replies requires human approval in the web UI."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": list_tools()})
    if method == "tools/call":
        name = params.get("name")
        if name not in EXPOSED_TOOLS and name not in EXTRA_TOOLS:
            return err(-32602, f"Unknown tool: {name}")
        try:
            payload, is_error = call_tool(name, params.get("arguments"))
        except Exception as e:  # noqa: BLE001 - tool failures are reported inside the result
            payload, is_error = {"error": f"{type(e).__name__}: {e}"}, True
        return ok({"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, default=str)}],
                   "isError": is_error})
    return err(-32601, f"Method not found: {method}")


def main():
    db.init_db()
    config.SIM_LATENCY = 0.0
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": None,
                                         "error": {"code": -32700, "message": "Parse error"}}) + "\n")
            sys.stdout.flush()
            continue
        resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    print("MCP server: stdin closed, exiting", file=sys.stderr)


if __name__ == "__main__":
    main()
