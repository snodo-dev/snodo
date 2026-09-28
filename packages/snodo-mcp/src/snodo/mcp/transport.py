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
import sys
import time
from datetime import datetime, timezone
from typing import Any, Optional

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import Context, FastMCP

from snodo.mcp.server import ProtocolMCPServer
from snodo.mcp.tools import TOOL_REGISTRY
from snodo.mcp.guide import guide_menu, guide_text
from snodo.mcp.watch_job import (
    WATCH_JOB_HTML, WATCH_JOB_RESOURCE_URI, WatchJobASGI, WatchLinkIssuer,
)

logger = logging.getLogger(__name__)

_MCP_LOG_MAX_CHARS = 240
_SECRET_KEY_RE = re.compile(
    r"(?i)(authorization|proxy-authorization|access-token|refresh-token|"
    r"client[_-]?(?:id|secret)|api[_-]?key|secret|password|tunnel[_-]?token|watch[_-]?token|"
    r"cf-access-client-(?:id|secret)|x-auth-token)"
)
_SECRET_VALUE_RES = (
    re.compile(r"(?i)(?<=/watch/)[A-Za-z0-9_.=-]{24,}"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9_\-.=+/]+"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{4,}\.[A-Za-z0-9_\-.]{4,}(?:\.[A-Za-z0-9_\-.]*)?"),
    re.compile(r"(?i)\b(?:sk|pk|rk|pat|ghp|gho|ghu|ghs|glpat|xox[baprs])-[A-Za-z0-9_-]{8,}\b"),
)


def _redact_mcp_value(value: Any) -> Any:
    """Recursively redact credential-shaped MCP payload fields and values."""
    if isinstance(value, dict):
        return {
            key: "[redacted]" if _SECRET_KEY_RE.search(str(key)) else _redact_mcp_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_mcp_value(item) for item in value]
    if isinstance(value, str):
        for pattern in _SECRET_VALUE_RES:
            value = pattern.sub("[redacted]", value)
        return value
    return value


def _safe_mcp_excerpt(value: Any) -> str:
    """Serialize and cap a payload excerpt after redacting secrets."""
    try:
        text = json.dumps(_redact_mcp_value(value), default=str, separators=(",", ":"))
    except Exception:  # noqa: BLE001 — diagnostics must never break serving
        text = "[unavailable]"
    text = " ".join(text.split())
    return text if len(text) <= _MCP_LOG_MAX_CHARS else text[:_MCP_LOG_MAX_CHARS] + "…"


def _enable_mcp_traffic_logging(mcp: FastMCP) -> None:
    """Log incoming JSON-RPC requests and their outgoing responses safely."""
    server = mcp._mcp_server
    original = server._handle_request

    async def logged_handle_request(message, req, session, lifespan_context, raise_exceptions):
        method = getattr(req, "method", type(req).__name__)
        if not isinstance(method, str):
            method = str(method)
        params = getattr(req, "params", None)
        params_data = params.model_dump(by_alias=True, exclude_none=True) if hasattr(params, "model_dump") else params
        request_id = getattr(message, "request_id", "-")
        tool_name = getattr(params, "name", None) if method == "tools/call" else None
        stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")
        label = f"{stamp} IN {method}"
        if tool_name:
            label += f" tool={tool_name}"
        label += f" id={request_id}"
        if params_data is not None:
            label += f" data={_safe_mcp_excerpt(params_data)}"
        print(sanitize_verbose_output(label), file=sys.stderr, flush=True)

        started = time.perf_counter()
        error = None
        response_holder = {}
        original_respond = message.respond

        async def capture_response(response):
            response_holder["response"] = response
            await original_respond(response)

        message.respond = capture_response
        try:
            await original(message, req, session, lifespan_context, raise_exceptions)
        except BaseException as exc:
            error = exc
            raise
        finally:
            response = response_holder.get("response")
            if error is None and response is not None:
                error_data = getattr(response, "error", None)
                error = error_data or (
                    response if hasattr(response, "code") and hasattr(response, "message") else None
                ) or (response if getattr(response, "isError", False) else None)
            status = "error" if error is not None else "ok"
            duration_ms = (time.perf_counter() - started) * 1000
            result_data = getattr(response, "root", response)
            if hasattr(result_data, "model_dump"):
                result_data = result_data.model_dump(by_alias=True, exclude_none=True)
            out = (
                f"{datetime.now(timezone.utc).astimezone().isoformat(timespec='milliseconds')} "
                f"OUT {method}"
            )
            if tool_name:
                out += f" tool={tool_name}"
            out += f" id={request_id} duration={duration_ms:.1f}ms {status}"
            if result_data is not None:
                out += f" data={_safe_mcp_excerpt(result_data)}"
            print(sanitize_verbose_output(out), file=sys.stderr, flush=True)

    server._handle_request = logged_handle_request

    original_notification = server._handle_notification

    async def logged_handle_notification(notification):
        method = getattr(notification, "method", type(notification).__name__)
        params = getattr(notification, "params", None)
        params_data = params.model_dump(by_alias=True, exclude_none=True) if hasattr(params, "model_dump") else params
        line = (
            f"{datetime.now(timezone.utc).astimezone().isoformat(timespec='milliseconds')} "
            f"IN {method} id=-"
        )
        if params_data is not None:
            line += f" data={_safe_mcp_excerpt(params_data)}"
        print(sanitize_verbose_output(line), file=sys.stderr, flush=True)
        await original_notification(notification)

    server._handle_notification = logged_handle_notification


def sanitize_verbose_output(line: str, secrets: tuple[str, ...] = ()) -> str:
    """Redact credential material before forwarding verbose child output."""
    text = " ".join((line or "").split())
    for pattern in _SECRET_VALUE_RES:
        text = pattern.sub("[redacted]", text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    text = re.sub(
        r"(?i)((?:authorization|cf-access-client-(?:id|secret)|api[_-]?key|"
        r"client[_-]?(?:id|secret)|tunnel[_-]?token)\s*[:=]\s*)([^,\s]+)",
        r"\1[redacted]", text,
    )
    return text


class _MissingAuthorizationObserver:
    """Name a protected HTTP refusal when the caller sent no credentials."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        has_authorization = b"authorization" in headers
        status = None

        async def observe_send(message):
            nonlocal status
            if message.get("type") == "http.response.start":
                status = message.get("status")
            await send(message)

        await self.app(scope, receive, observe_send)
        if status == 401 and not has_authorization:
            logger.warning("MCP authentication refused: Authorization header missing")


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
    validator_list = ", ".join(v.validator_id for v in p.validators)
    policy_value = getattr(p.disagreement_policy, "value", str(p.disagreement_policy))

    sections: list = [
        f"Call the read-only `guide` tool before anything else. It teaches the shortest path and accepts topics: {guide_menu(protocol_server.project_root)}.\n",
        f"# Snodo Protocol Engine — {p.protocol_id} v{p.version}\n",
        f"Serving mode: `{protocol_server._active_mode()}`.\n",
        "You are the orchestrator. Use MCP tools and resources only; you cannot read the filesystem directly.\n",
        "Choose the smallest structure that fits: dispatch one task directly; use a wave only for multiple tasks that can run together; use a plan only for multiple waves; use a queue only to schedule several plans. A one-task plan adds plan→wave→task history for cloud reporting, but does not add a human authorization gate, change the task validator loop, or change auto-merge policy.\n",
        "Tool access follows the active mode grant; no tool call is refused for want of a caller-held token. The validator quorum is enforced inside the engine loop (ADR 047); a `blocker` is never overridable, and `escalate` requires human `snodo authorize`.\n",
    ]

    if "dispatch_task" in exposed:
        sections.append(
            "\n"
            "## Task loop\n"
            "1. `validate_task(task_id, task_spec)` runs pre-execute validators and returns `pass`, `escalate`, `blocker`, or `validator_error`. `escalate` needs human `snodo authorize`; `blocker` needs a fix or better evidence/spec; `validator_error` needs retry or inspection.\n"
            "2. `dispatch_task(task_spec)` submits background work.\n"
        )

    if "run_plan" in exposed:
        sections.append(
            "\n"
            "## Planning\n"
            "`propose_plan` creates the inert plan, `generate_spec` adds named task specs (name `module` when one declared module owns the task), and `validate_plan` checks structure and references (not an authorization gate). `run_plan` starts a background run. Use `get_plan` for per-task state; a wave is a barrier, so dependent tasks belong in later waves.\n"
        )

    if "queue_run" in exposed:
        sections.append(
            "\n## Queues\n"
            "Use `queue_list` and `queue_validate` to inspect ordered queues, `queue_create` / `queue_move` / `queue_remove` to manage them, and `queue_run` to progress them (validate, reorder, unblock, run).\n"
        )

    if "dispatch_task" in exposed or "run_plan" in exposed or "queue_run" in exposed:
        async_lines = [
            "\n"
            "## Async contract\n"
        ]
        if "dispatch_task" in exposed:
            async_lines.append(
                "`dispatch_task` is ASYNCHRONOUS. A validation pass is not task success; only `completed` with `exit_code=0` confirms it.\n"
                "\n"
            )
        if "run_plan" in exposed:
            async_lines.append(
                "`run_plan` is ASYNCHRONOUS and returns its job id immediately; a wave takes minutes.\n"
                "\n"
            )
        if "queue_run" in exposed:
            async_lines.append("`queue_run` is ASYNCHRONOUS and returns its job id immediately.\n\n")
        async_lines.append(
            "After a job starts, call `watch_job(job_id)`. Hand the operator its browser link when returned; otherwise use `snodo logs <job_id> --watch`. The text snapshot is always available. The optional MCP Apps panel renders only in some hosts, so do not rely on it. Use `get_job_status` / `get_job_logs` only for a specific follow-up. The starter response only confirms queuing.\n"
        )
        sections.append("".join(async_lines))

    progress_lines = [
        "\n"
        "## Progress\n"
        "`validate_task` may take minutes and can emit MCP progress notifications when the caller supplies a progress token.\n"
    ]
    if "run_plan" in exposed:
        progress_lines.append(
            "`run_plan` returns a job_id at once. With `wait=true` and a progress token it narrates task status changes; job logs hold the full narration.\n"
        )
    if "queue_run" in exposed:
        progress_lines.append("`queue_run` returns a job_id at once; inspect the runner's output through job logs.\n")
    sections.append("".join(progress_lines))

    guarantee_lines = [
        "\n"
        "## Governance\n"
    ]
    if "dispatch_task" in exposed:
        guarantee_lines.append(
            "- `validate_task` records a single-use token on `pass`; `dispatch_task` consumes it as the audit link.\n"
        )
    else:
        guarantee_lines.append(
            "- `validate_task` records a single-use token on `pass`; the engine dispatch boundary consumes it as the audit link.\n"
        )
    guarantee_lines.append(
        "- No tool call is gated on a caller-held token. Mode grants determine this surface; the loop validates before execution.\n"
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
        f"## Active protocol\n- Protocol ID: {p.protocol_id}\n- Version: {p.version}\n- Modes: {mode_list}\n- Validators: {validator_list}\n- Disagreement policy: {policy_value}\n"
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
    verbose: bool = False,
    public_base_url: Optional[str] = None,
    watch_link_ttl: int = 24 * 60 * 60,
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
    watch_issuer = WatchLinkIssuer(watch_link_ttl) if public_base_url else None

    mcp = FastMCP(
        server_name,
        instructions=instructions,
        token_verifier=token_verifier,
        auth=auth_settings,
    )

    for tool_info in protocol_server.get_tools():
        fn = _make_tool_handler(protocol_server, tool_info)
        if tool_info["name"] == "watch_job":
            fn = _with_browser_watch_link(fn, public_base_url, watch_issuer)
        tool_meta = (
            {"ui": {"resourceUri": WATCH_JOB_RESOURCE_URI}}
            if tool_info["name"] == "watch_job"
            else None
        )
        mcp.add_tool(
            fn,
            name=tool_info["name"],
            description=tool_info["description"],
            meta=tool_meta,
        )

    _register_resources(mcp, protocol_server)
    if "watch_job" in {tool["name"] for tool in protocol_server.get_tools()}:
        _register_watch_job_resource(mcp)
    _register_guide(mcp, protocol_server)

    if verbose:
        _enable_mcp_traffic_logging(mcp)

    # An absent header never reaches TokenVerifier.verify_token, so the
    # verifier cannot explain that 401. Observe protected HTTP app responses
    # without retaining or printing any header values.
    for app_factory_name in ("streamable_http_app", "sse_app"):
        app_factory = getattr(mcp, app_factory_name)

        def observed_app_factory(*args, _factory=app_factory, **kwargs):
            return _MissingAuthorizationObserver(_factory(*args, **kwargs))

        setattr(mcp, app_factory_name, observed_app_factory)

    if watch_issuer is not None:
        for app_factory_name in ("streamable_http_app", "sse_app"):
            app_factory = getattr(mcp, app_factory_name)

            def watch_app_factory(*args, _factory=app_factory, **kwargs):
                return WatchJobASGI(
                    _factory(*args, **kwargs), protocol_server, watch_issuer, public_base_url,
                )

            setattr(mcp, app_factory_name, watch_app_factory)

    return mcp


def _with_browser_watch_link(handler, base_url, issuer):
    """Append the bearer capability to watch_job's user-facing result only."""
    import inspect

    async_handler = inspect.iscoroutinefunction(handler)

    def decorate(result, arguments):
        if issuer is None or not base_url:
            return f"{result}\n\nNo reachable browser URL is configured; follow with: snodo logs {arguments.get('job_id', '')} --watch"
        job_id = arguments.get("job_id", "")
        token = issuer.issue(job_id)
        return f"{result}\n\nLive browser view (expires in {issuer.ttl_seconds} seconds): {base_url.rstrip('/')}/watch/{token}"

    if async_handler:
        async def wrapped(**kwargs):
            result = await handler(**kwargs)
            return decorate(result, kwargs)
    else:
        def wrapped(**kwargs):
            return decorate(handler(**kwargs), kwargs)
    wrapped.__name__ = handler.__name__
    wrapped.__doc__ = handler.__doc__
    wrapped.__signature__ = getattr(handler, "__signature__", inspect.signature(handler))
    wrapped.__annotations__ = getattr(handler, "__annotations__", {})
    return wrapped


def _register_guide(mcp: FastMCP, protocol_server: ProtocolMCPServer) -> None:
    """Register the always-available, read-only source-backed guide."""
    exposed = {t["name"] for t in protocol_server.get_tools()}

    @mcp.tool(
        name="guide",
        description=f"Read-only Snodo getting-started guide. Omit topic for the shortest first run; ask for {guide_menu(protocol_server.project_root)}.",
    )
    def guide(topic: str | None = None) -> str:
        mode_id = protocol_server._active_mode()
        return f"Serving mode: {mode_id}.\n\n" + guide_text(
            protocol_server.project_root, exposed, topic
        )


def _register_watch_job_resource(mcp: FastMCP) -> None:
    """Register the self-contained MCP Apps view only when its tool is granted."""

    @mcp.resource(
        WATCH_JOB_RESOURCE_URI,
        name="watch_job_view",
        description="Live job output view for watch_job",
        mime_type="text/html;profile=mcp-app",
        meta={"ui": {"csp": {}, "prefersBorder": True}},
    )
    def watch_job_view() -> str:
        return WATCH_JOB_HTML


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
