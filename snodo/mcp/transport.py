"""FastMCP transport bridge for Snodo MCP server.

FILE: snodo/mcp/transport.py

Bridges ProtocolMCPServer (tool resolution, WF1 enforcement) to FastMCP
(official MCP SDK transport). Replaces the custom stdio/SSE transport
that didn't work with Claude Desktop.

ProtocolMCPServer handles:
- Protocol-driven tool resolution (MODE_TOOL_MAP -> TOOL_REGISTRY)
- WF1 enforcement (validation tokens for mutating tools)
- Dispatching to backing MCPs (workspace, git, shell, pr, planner)

FastMCP handles:
- MCP protocol handshake (initialize, notifications)
- JSON-RPC framing (Content-Length headers, stdio)
- Tool listing and calling via MCP protocol
- Server instructions and resources (self-description)
"""

import inspect
import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from snodo.mcp.server import ProtocolMCPServer


_JSON_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


def _build_instructions(protocol_server: ProtocolMCPServer) -> str:
    """Build the canonical operating manual from the loaded protocol.

    This lands in the MCP initialize handshake — present in every session,
    before any tool call. It is the orchestrator's only source of truth
    about the workflow, role model, and async contract.
    """
    p = protocol_server.protocol
    mode_list = ", ".join(m.mode_id for m in p.modes)
    validator_list = ", ".join(
        f"{v.validator_id} ({v.validator_type}, {v.evaluation_phase})"
        for v in p.validators
    )
    policy_value = getattr(p.disagreement_policy, "value", str(p.disagreement_policy))

    return (
        f"# Snodo Protocol Engine — {p.protocol_id} v{p.version}\n"
        f"\n"
        f"## What this is\n"
        f"A protocol-driven AI software development lifecycle (AI-SDLC) engine.\n"
        f"You are the orchestrator — you coordinate via MCP tools only. You have NO\n"
        f"direct filesystem access. All knowledge about the project, sessions, and\n"
        f"audit trail comes through tools and resources (see resources below).\n"
        f"\n"
        f"## Role model\n"
        f"- **Orchestrator (you)**: coordinates workflow via tools. Never writes files.\n"
        f"- **Coder**: generates code artifacts. Runs in background jobs.\n"
        f"- **Validators**: read-only checks on task specs (pre-execute) and code\n"
        f"  changes (post-execute). They cannot mutate the repo.\n"
        f"- **Mutations are token-gated (WF1)**: write/exec tools require a single-use\n"
        f"  validation token issued by a satisfied validator quorum.\n"
        f"\n"
        f"## The workflow loop (per task)\n"
        f"Execute tasks in this exact order:\n"
        f"\n"
        f"1. `validate_task(task_id)` — runs pre-execute validators, returns results + token\n"
        f"2. `dispatch_task(task_spec)` — submits task for background execution, returns job_id\n"
        f"3. `get_job_status(job_id)` — poll until status is `completed` or `failed`\n"
        f"4. `get_job_logs(job_id, tail=N)` — read output, especially on failure\n"
        f"\n"
        f"## THE ASYNC CONTRACT — READ THIS\n"
        f"**dispatch_task is ASYNCHRONOUS.** It returns a job_id and returns IMMEDIATELY.\n"
        f"The coder runs in a background subprocess. A pre-execute validation pass does\n"
        f"NOT mean the task succeeded. Only a job whose status is `completed` with\n"
        f"`exit_code=0` and files written confirms success.\n"
        f"\n"
        f"**ALWAYS poll `get_job_status` after dispatch. NEVER infer completion from the\n"
        f"dispatch response.** The dispatch response only confirms the job was queued.\n"
        f"\n"
        f"## WF1 token lifecycle\n"
        f"- `validate_task` issues a single-use JWT token with a short TTL.\n"
        f"- The token authorizes the next mutating tool call (write_file, commit, etc).\n"
        f"- The token is consumed on use — you must re-validate for each mutation cycle.\n"
        f"\n"
        f"## Where to find state\n"
        f"You cannot read the filesystem. Use these resources instead:\n"
        f"- `snodo://protocol` — modes, validators, constraints, disagreement policy\n"
        f"- `snodo://sessions` — list of all sessions with status\n"
        f"- `snodo://sessions/{{session_id}}` — session detail: task history, events, results\n"
        f"- `snodo://audit` — recent audit events (last 100)\n"
        f"\n"
        f"## Active protocol\n"
        f"- Protocol ID: {p.protocol_id}\n"
        f"- Version: {p.version}\n"
        f"- Modes: {mode_list}\n"
        f"- Validators: {validator_list}\n"
        f"- Disagreement policy: {policy_value}\n"
    )


def build_fastmcp_server(protocol_server: ProtocolMCPServer) -> FastMCP:
    """Build a FastMCP server that delegates to a ProtocolMCPServer.

    Creates a FastMCP instance with tools matching the protocol configuration,
    instructions from the loaded protocol, and resources for self-description.

    Args:
        protocol_server: ProtocolMCPServer with resolved tools and WF1 state

    Returns:
        Configured FastMCP instance ready to run
    """
    server_name = f"snodo-{protocol_server.protocol.protocol_id}"
    if protocol_server.mode_id:
        server_name += f"-{protocol_server.mode_id}"

    instructions = _build_instructions(protocol_server)

    mcp = FastMCP(server_name, instructions=instructions)

    for tool_info in protocol_server.get_tools():
        fn = _make_tool_handler(protocol_server, tool_info)
        mcp.add_tool(fn, name=tool_info["name"], description=tool_info["description"])

    _register_resources(mcp, protocol_server)

    return mcp


def _register_resources(mcp: FastMCP, protocol_server: ProtocolMCPServer) -> None:
    """Register read-only resources for orchestrator self-description.

    Resources are URI-addressable data backed by existing managers — no new logic.
    """

    @mcp.resource(
        "snodo://protocol",
        name="protocol",
        description="Protocol definition: modes, validators, constraints, disagreement policy",
        mime_type="application/json",
    )
    def get_protocol() -> str:
        return json.dumps(
            protocol_server.protocol.model_dump(),
            default=str,
            indent=2,
        )

    @mcp.resource(
        "snodo://sessions",
        name="sessions",
        description="List of all sessions with id, mode, current task, and updated timestamp",
        mime_type="application/json",
    )
    def get_sessions() -> str:
        from snodo.infrastructure.session import SessionManager

        mgr = SessionManager()
        sessions = mgr.list_sessions(project_root=protocol_server.project_root)
        return json.dumps(
            [
                {
                    "session_id": s.session_id,
                    "mode": s.mode,
                    "current_task": s.checkpoint.current_task,
                    "created_at": s.created_at,
                    "updated_at": s.updated_at,
                }
                for s in sessions
            ],
            default=str,
            indent=2,
        )

    @mcp.resource(
        "snodo://sessions/{session_id}",
        name="session-detail",
        description="Session detail: ordered task list, validator results, events",
        mime_type="application/json",
    )
    def get_session_detail(session_id: str) -> str:
        from snodo.infrastructure.session import SessionManager

        mgr = SessionManager()
        try:
            session = mgr.load_session(session_id)
        except FileNotFoundError:
            return json.dumps({"error": f"Session not found: {session_id}"})

        # Get audit events for this session
        audit_events = []
        audit_log = protocol_server._audit_log
        if audit_log is not None:
            all_events = audit_log.get_history()
            for ev in all_events:
                data = ev.data if isinstance(ev.data, dict) else {}
                if data.get("session_id") == session_id:
                    audit_events.append({
                        "sequence": ev.sequence,
                        "timestamp": ev.timestamp,
                        "event_type": ev.event_type,
                        "data": data,
                    })

        return json.dumps(
            {
                "session_id": session.session_id,
                "mode": session.mode,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "current_task": session.checkpoint.current_task,
                "memory_summary": session.checkpoint.memory_summary,
                "decisions": session.checkpoint.decisions,
                "audit_events": audit_events[-100:],
            },
            default=str,
            indent=2,
        )

    @mcp.resource(
        "snodo://audit",
        name="audit",
        description="Recent audit events (last 100)",
        mime_type="application/json",
    )
    def get_audit() -> str:
        audit_log = protocol_server._audit_log
        if audit_log is None:
            return json.dumps({"events": [], "note": "No audit log available"})

        events = audit_log.get_history()[-100:]
        return json.dumps(
            [
                {
                    "sequence": ev.sequence,
                    "timestamp": ev.timestamp,
                    "event_type": ev.event_type,
                    "data": ev.data if isinstance(ev.data, dict) else {},
                }
                for ev in events
            ],
            default=str,
            indent=2,
        )


def _make_tool_handler(
    protocol_server: ProtocolMCPServer, tool_info: dict
) -> Any:
    """Create a tool handler function with proper signature for FastMCP.

    FastMCP inspects function signatures to generate input schemas.
    We build a function with the correct parameters matching our
    TOOL_REGISTRY schema so Claude sees proper parameter descriptions.

    Args:
        protocol_server: Server to delegate tool calls to
        tool_info: Tool descriptor with name, description, inputSchema

    Returns:
        A callable with proper __signature__ for FastMCP inspection
    """
    tool_name = tool_info["name"]
    schema = tool_info["inputSchema"]
    properties = schema.get("properties", {})
    required_set = set(schema.get("required", []))

    # Build inspect.Parameter list from JSON Schema
    params = []
    annotations = {}

    for pname, pinfo in properties.items():
        ptype = _JSON_TYPE_MAP.get(pinfo.get("type", "string"), str)
        annotations[pname] = ptype

        if pname in required_set:
            params.append(inspect.Parameter(
                pname, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=ptype,
            ))
        else:
            default = pinfo.get("default", None)
            params.append(inspect.Parameter(
                pname, inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default, annotation=ptype,
            ))

    annotations["return"] = str

    # Create handler closure that delegates to protocol server
    is_slow = protocol_server.is_slow_tool(tool_name)

    if is_slow:
        async def handler(**kwargs) -> str:
            result = await protocol_server.call_tool_async(tool_name, kwargs)
            if isinstance(result, str):
                return result
            return json.dumps(result, default=str)
    else:
        def handler(**kwargs) -> str:
            result = protocol_server.call_tool(tool_name, kwargs)
            if isinstance(result, str):
                return result
            return json.dumps(result, default=str)

    handler.__name__ = tool_name
    handler.__doc__ = tool_info["description"]
    handler.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    handler.__annotations__ = annotations

    return handler
