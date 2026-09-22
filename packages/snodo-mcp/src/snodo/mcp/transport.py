"""FastMCP transport bridge for Snodo MCP server.

FILE: snodo/mcp/transport.py

Bridges ProtocolMCPServer (tool resolution, mode filtering) to FastMCP
(official MCP SDK transport). Replaces the custom stdio/SSE transport
that didn't work with Claude Desktop.

ProtocolMCPServer handles:
- Protocol-driven tool resolution (MODE_TOOL_MAP -> TOOL_REGISTRY)
- Validation-quorum reporting (validate_task outcome; single-use token
  recorded on a pass and consumed at the dispatch boundary — an audit
  link, not a gate the caller must pass; see ADR 047)
- Dispatching to backing MCPs (workspace, git, shell, pr, planner)

FastMCP handles:
- MCP protocol handshake (initialize, notifications)
- JSON-RPC framing (Content-Length headers, stdio)
- Tool listing and calling via MCP protocol
- Server instructions and resources (self-description)
"""

import asyncio
import inspect
import json
import logging
import re
from typing import Any, Optional

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP

from snodo.mcp.server import ProtocolMCPServer
from snodo.mcp.tools import TOOL_REGISTRY

logger = logging.getLogger(__name__)


_JSON_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}


# ── Progress notifications ──────────────────────────────────────────────
#
# The SDK already owns both ends of this wire and nothing here reinvents
# them: a client that passes a progress_callback to tools/call has the SDK
# stamp params._meta.progressToken on the request, and Context.report_progress
# emits notifications/progress — or returns WITHOUT sending when no token was
# supplied, which is the "emit nothing when unasked" rule enforced by the
# protocol layer, not re-implemented here. What this module adds is a
# DESTINATION for narration that already exists: the validator runner's
# progress lines, bridged from worker threads onto the event loop. Only
# tools with real in-call narration join (server._PROGRESS_TOOLS); a call
# that returns a job id immediately has nothing to carry, and keeps no
# mechanism for its own sake.
#
# Invariants held here:
# - Progress is not a result. The emitter only sends notifications; the
#   tool's final response is produced exactly as before, and a caller that
#   ignores every notification gets today's response byte for byte.
# - A notification carries what a human would want to READ (a validator
#   beginning, a turn taken, a wave's task completing) — never payloads.
#   Bodies are single-line, length-capped, secret-redacted, and
#   diff-framing-shaped lines are dropped outright.
# - A notification is an observer's job: an emit that raises (loop closing,
#   transport dying, SDK bug) is logged and dropped, never propagated into
#   the run being narrated.

#: Trailing characters of a narration line that survive into a notification.
_PROGRESS_MAX_CHARS = 240

#: Lines shaped like diff framing are payload, not narration — dropped.
_PROGRESS_DROP_PREFIXES = ("diff --git ", "@@", "+++", "--- ", "---\n", "index ")

#: JWT-ish ("eyJ..." with at least one dot segment) and common API-key /
#: bearer forms. Redaction is defense-in-depth over the narration sources
#: (which never intend to print a secret); a coder that echoes one into its
#: stdout still cannot leak it through a progress notification.
_PROGRESS_SECRET_RES = (
    re.compile(r"\beyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-.]{4,}"),
    re.compile(r"\b(?:sk|pat|ghp|gho|ghu|ghs|gho|glpat|xox[baprse])-[A-Za-z0-9][A-Za-z0-9\-]{6,}\b"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-_\.=+/]{10,}"),
)


def _sanitize_progress_line(line: str) -> Optional[str]:
    """Reduce one narration line to a safe single-line notification body.

    Returns None when the line carries nothing a caller should see (blank,
    diff framing) or nothing left after redaction.
    """
    text = " ".join((line or "").split())
    if not text:
        return None
    if text.startswith(_PROGRESS_DROP_PREFIXES):
        return None
    for pattern in _PROGRESS_SECRET_RES:
        text = pattern.sub("[redacted]", text)
    if not text:
        return None
    if len(text) > _PROGRESS_MAX_CHARS:
        text = "…" + text[-_PROGRESS_MAX_CHARS:]
    return text


def _make_progress_emitter(ctx: Optional[Context]) -> tuple:
    """Return ``(emit, drain)`` bridging narration to MCP progress notifications.

    *emit* is a plain ``callable(line: str)`` usable from ANY thread — the
    validator runner narrates from its own thread pool, not the event
    loop's thread, so each notification is scheduled onto the loop with
    ``run_coroutine_threadsafe`` (fire-and-forget, FIFO-ordered). *drain* is
    awaited by the handler before returning so every queued notification
    lands while the request is still in flight — notifications never race
    the response they describe.

    Returns ``(None, noop)`` when the caller supplied no progress token: the
    handlers then behave exactly as they do today, with no sink to feed.
    """
    async def _no_drain() -> None:
        return None

    meta = getattr(getattr(ctx, "request_context", None), "meta", None)
    if meta is None or getattr(meta, "progressToken", None) is None:
        return None, _no_drain
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover — the handler always runs on a loop
        return None, _no_drain

    state = {"n": 0}
    futures: list = []

    def emit(line: str) -> None:
        message = _sanitize_progress_line(line)
        if message is None:
            return
        state["n"] += 1
        try:
            futures.append(asyncio.run_coroutine_threadsafe(
                ctx.report_progress(float(state["n"]), None, message),
                loop,
            ))
        except Exception as e:  # noqa: BLE001 — an observer must not kill its subject
            logger.debug("progress notification dropped: %s: %s", type(e).__name__, e)

    async def drain() -> None:
        if not futures:
            return
        pending = list(futures)
        del futures[:]
        try:
            await asyncio.gather(
                *(asyncio.wrap_future(f) for f in pending),
                return_exceptions=True,
            )
        except Exception as e:  # noqa: BLE001 — same rule: never fatal
            logger.debug("progress drain failed: %s: %s", type(e).__name__, e)

    return emit, drain


def _build_instructions(protocol_server: ProtocolMCPServer) -> str:
    """Build the canonical operating manual from the loaded protocol AND the
    tools this server actually exposes.

    This lands in the MCP initialize handshake — present in every session,
    before any tool call. It is the orchestrator's only source of truth
    about the workflow, role model, and async contract — and about the tool
    set it may trust: which tools exist is the mode grant's decision, so the
    manual cannot be a fixed string. A fixed manual tells the orchestrator
    to call tools it was never given (the defect this closes: a server
    offering run_plan, withholding get_job_status, and instructing the
    caller to poll it). Sections are therefore emitted over the server's
    resolved tool set, and a final guard refuses to serve a manual that
    names any tool this server does not expose.

    The guard holds because of the server-side invariant
    (ProtocolMCPServer._resolve_tools / tools.WORK_STARTING_TOOLS): every
    work-starting tool travels with its read-only observers, so a section
    gated on a starter can name that starter's observers unconditionally.
    """
    p = protocol_server.protocol
    exposed = {t["name"] for t in protocol_server.get_tools()}
    mode_list = ", ".join(m.mode_id for m in p.modes)
    validator_list = ", ".join(
        f"{v.validator_id} ({v.validator_type}, {v.evaluation_phase})"
        for v in p.validators
    )
    policy_value = getattr(p.disagreement_policy, "value", str(p.disagreement_policy))

    sections: list = [
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
        f"- **Tool access is the protocol's to decide**: a server exposes only the\n"
        f"  tools the active mode(s) grant, and no tool call is refused for want\n"
        f"  of a token the caller holds (see ADR 047).\n"
        f"- **The quorum is enforced inside the engine loop**: every dispatched\n"
        f"  task passes the validators again before anything is written — a\n"
        f"  `blocker` sends the work back and is never overridable by you.\n"
    ]

    if "dispatch_task" in exposed:
        sections.append(
            "\n"
            "## The workflow loop (per task)\n"
            "Execute tasks in this exact order:\n"
            "\n"
            "1. `validate_task(task_id, task_spec)` — runs the real pre-execute validators\n"
            "   and the test suite, then returns ONE of four validation outcomes:\n"
            "   - `pass`            → quorum satisfied (a single-use token is recorded);\n"
            "                        proceed to dispatch\n"
            "   - `escalate`        → NO token; run `snodo authorize <decision_id>`, then\n"
            "                        re-call validate_task to clear it\n"
             "   - `blocker`         → NO token; fix the code or, when verification is\n"
             "                        unavailable, run recon and make the task spec smaller\n"
             "                        or more detailed, then re-validate (never overridable)\n"
            "                        NEVER overridable by a human decision)\n"
            "   - `validator_error` → NO token; retry / inspect logs (not an authorisation\n"
            "                        problem)\n"
            "   The engine's canonical halt vocabulary is five. A dispatched job can\n"
            "   additionally halt `environment_error` (the coder could not be invoked);\n"
            "   handle it as a non-verdict operational halt, never as a verdict about the\n"
            "   task. See ADR 015 for the taxonomy and the reasoning.\n"
            "2. `dispatch_task(task_spec)` — submits the task for background execution,\n"
            "   returns job_id; the run re-validates this task inside the engine loop\n"
            "   before anything is written, so your pre-check is guidance, not a\n"
            "   permission the call must carry\n"
            "3. `get_job_status(job_id)` — poll until status is `completed` or `failed`\n"
            "4. `get_job_logs(job_id, tail=N)` — read output, especially on failure\n"
        )

    if "run_plan" in exposed:
        sections.append(
            "\n"
            "## Planning (above the task loop)\n"
            "Multi-task work goes through the plan gate before any dispatch:\n"
            "- `propose_plan(intent, plan_name)` turns an intent into a plan (waves,\n"
            "  dependencies, tasks) under .snodo/plans/ — nothing executes.\n"
            "- `generate_spec(plan_name, task_id, spec)` writes a task spec into a wave. "
            "Task IDs must carry a name as `<wave>.<sequence>_<name>` (for example "
            "`1.1_models`, never bare `1.1`); the name becomes the readable spec "
            "filename and progress label.\n"
            "- `validate_plan(plan_name)` checks the plan without running anything; this\n"
            "  is the human gate — review the proposal BEFORE execution.\n"
            "- `run_plan(plan_name)` STARTS an approved plan run as a background job\n"
            "  and returns its job_id at once; it refuses a plan that fails\n"
            "  validation before anything spawns. Follow the job_id with\n"
            "  `get_job_status` / `list_jobs` / `get_job_logs` — do NOT expect the call to\n"
            "  carry the run (a wave takes minutes).\n"
            "- `get_plan(plan_name)` retrieves the plan and its task statuses at any\n"
            "  time — the plan files on disk are the source of truth.\n"
            "- `record_task_status(plan_name, task_id, status, who, notes)` records\n"
            "  a status an operator decided on outside the loop — the machine-side\n"
            "  `snodo task complete`. It writes the plan's own status vocabulary\n"
            "  and appends an unjudged audit event so the plan advances from the\n"
            "  record. It records a human's account and decides nothing: it never\n"
            "  passes a task in place of the validators.\n"
        )

    if "dispatch_task" in exposed or "run_plan" in exposed:
        async_lines = [
            "\n"
            "## THE ASYNC CONTRACT — READ THIS\n"
        ]
        if "dispatch_task" in exposed:
            async_lines.append(
                "**dispatch_task is ASYNCHRONOUS.** It returns a job_id and returns IMMEDIATELY.\n"
                "The coder runs in a background subprocess. A pre-execute validation pass does\n"
                "NOT mean the task succeeded. Only a job whose status is `completed` with\n"
                "`exit_code=0` and files written confirms success.\n"
                "\n"
            )
        if "run_plan" in exposed:
            async_lines.append(
                "**run_plan is ASYNCHRONOUS.** It starts the plan run as a background job and\n"
                "returns its job_id IMMEDIATELY — a wave takes minutes; the call does not\n"
                "carry the run.\n"
                "\n"
            )
        async_lines.append(
            "**ALWAYS poll `get_job_status` after a job starts. NEVER infer completion from the\n"
            "response of the tool that started it.** That response only confirms the job was\n"
            "queued.\n"
        )
        sections.append("".join(async_lines))

    progress_lines = [
        "\n"
        "## Progress on slow calls\n"
        "`validate_task` can take minutes. It honours MCP progress notifications:\n"
        "include `\"_meta\": {\"progressToken\": \"<your-token>\"}` in the tools/call\n"
        "params and the server narrates validators starting/finishing and their\n"
        "per-turn tool lines as `notifications/progress` while the call is in\n"
        "flight, so a slow call is distinguishable from a dead server. Callers that\n"
        "do not request progress receive nothing extra and the same final response.\n"
    ]
    if "run_plan" in exposed:
        progress_lines.append(
            "(`run_plan` needs no progress stream: it returns a job_id at once, and the\n"
            "run's narration lands in the job's stdout.log as it is produced\n"
            "(get_job_logs, `snodo job logs --watch`).)\n"
        )
    sections.append("".join(progress_lines))

    guarantee_lines = [
        "\n"
        "## Where the guarantee lives (tokens and access)\n"
    ]
    if "dispatch_task" in exposed:
        guarantee_lines.append(
            "- `validate_task` runs the pre-execute quorum. On `pass` (or on\n"
            "  `escalate` after a human adjudicates via `snodo authorize`) it records a\n"
            "  single-use JWT token with a short TTL; the next `dispatch_task` consumes\n"
            "  it — the audit link between a satisfied quorum and the work dispatched.\n"
        )
    else:
        guarantee_lines.append(
            "- `validate_task` runs the pre-execute quorum. On `pass` (or on\n"
            "  `escalate` after a human adjudicates via `snodo authorize`) it records a\n"
            "  single-use JWT token with a short TTL; the engine's dispatch boundary\n"
            "  consumes it — the audit link between a satisfied quorum and the work\n"
            "  dispatched.\n"
        )
    guarantee_lines.append(
        "- No tool at this surface is gated on a token you hold. Your authority is\n"
        "  the protocol's mode grant: a server exposes only the tools its active\n"
        "  mode(s) grant, and refuses everything else (see ADR 047).\n"
        "- The enforceable discipline is per task inside the engine loop: the run\n"
        "  validates before it executes; a `blocker` sends the work back and is\n"
        "  never overridable; an `escalate` halts until a human decides through\n"
        "  `snodo authorize`. None of that can be bypassed from this surface.\n"
    )
    sections.append("".join(guarantee_lines))

    sections.append(
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

    instructions = "".join(sections)
    _assert_names_only_exposed_tools(instructions, exposed)
    return instructions


def _assert_names_only_exposed_tools(instructions: str, exposed: set) -> None:
    """Refuse to serve a manual that names a tool this server does not expose.

    The sections above are built over the resolved tool set, so this should
    never fire; it exists because an instruction that lies is not a hint the
    orchestrator can ignore — it is a contract the server cannot honour. If
    a future edit names an unexposed tool in prose, the server fails at
    build time, not the caller at run time.
    """
    named = {
        name for name in TOOL_REGISTRY
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", instructions)
    }
    missing = named - exposed
    if missing:
        raise RuntimeError(
            "Server instructions name tools this server does not expose: "
            + ", ".join(sorted(missing))
            + " — the manual must describe the tool set the server has."
        )


def build_fastmcp_server(
    protocol_server: ProtocolMCPServer,
    *,
    token_verifier: Optional[TokenVerifier] = None,
    auth_settings: Optional[AuthSettings] = None,
) -> FastMCP:
    """Build a FastMCP server that delegates to a ProtocolMCPServer.

    Creates a FastMCP instance with tools matching the protocol configuration,
    instructions from the loaded protocol, and resources for self-description.

    Args:
        protocol_server: ProtocolMCPServer with resolved tools and validation state
        token_verifier: Optional OAuth TokenVerifier for Bearer JWT validation.
            When set, FastMCP wraps the /mcp endpoint with auth middleware.
        auth_settings: Optional OAuth settings (issuer_url, resource_server_url).
            When set with resource_server_url, FastMCP serves
            /.well-known/oauth-protected-resource for client discovery.

    Returns:
        Configured FastMCP instance ready to run
    """
    server_name = f"snodo-{protocol_server.protocol.protocol_id}"
    if protocol_server.mode_id:
        server_name += f"-{protocol_server.mode_id}"

    instructions = _build_instructions(protocol_server)

    mcp = FastMCP(
        server_name,
        instructions=instructions,
        token_verifier=token_verifier,
        auth=auth_settings,
    )

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
    narrates = is_slow and protocol_server.accepts_progress(tool_name)

    if narrates:
        # FastMCP finds the context parameter through the function's type
        # hints (mcp.server.fastmcp.utilities.context_injection): declaring
        # ``ctx`` in __annotations__ below makes the SDK pass the request
        # Context as a keyword argument AFTER schema validation — it never
        # touches the client-visible input schema, which is built from the
        # synthesized __signature__ that carries only the tool's own params.
        async def handler(**kwargs) -> str:
            emit, drain = _make_progress_emitter(kwargs.pop("ctx", None))
            try:
                result = await protocol_server.call_tool_async(
                    tool_name, kwargs, progress_sink=emit,
                )
            finally:
                await drain()
            if isinstance(result, str):
                return result
            return json.dumps(result, default=str)
    elif is_slow:
        async def handler(**kwargs) -> str:
            result = await protocol_server.call_tool_async(tool_name, kwargs)
            if isinstance(result, str):
                return result
            return json.dumps(result, default=str)
    else:
        def handler(**kwargs) -> str:  # type: ignore[misc]
            result = protocol_server.call_tool(tool_name, kwargs)
            if isinstance(result, str):
                return result
            return json.dumps(result, default=str)

    handler.__name__ = tool_name
    handler.__doc__ = tool_info["description"]
    if narrates:
        annotations["ctx"] = Context
    handler.__signature__ = inspect.Signature(params, return_annotation=str)  # type: ignore[attr-defined]
    handler.__annotations__ = annotations

    return handler
