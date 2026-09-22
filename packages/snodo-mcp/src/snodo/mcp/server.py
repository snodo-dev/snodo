"""Protocol-Driven MCP Server.

Generates an MCP server from a Protocol definition:
- Maps protocol mode tools to real MCP implementations (workspace, git, shell)
- Filters available tools by active mode — tool access is the protocol's
  and the mode's to decide, not a token the caller must hold (see ADR 047)
- Records the validator quorum's verdict: validate_task mints a single-use
  token on a pass and dispatch_task consumes it as the audit link between
  a satisfied quorum and the work dispatched

Transport is handled by FastMCP (see transport.py).
"""

import asyncio
import functools
import hashlib
import importlib.metadata
import logging
import threading
from typing import Any, Dict, List, Optional

from snodo.compiler.models import Protocol
from snodo.infrastructure.tokens import TokenIssuer, TokenStoreError, ValidationToken
from snodo.core.interfaces import Task, result_record
from snodo.core.spec import same_spec, spec_with_guidance
from snodo.tools.workspace import WorkspaceMCP
from snodo.tools.git import GitMCP
from snodo.tools.shell import ShellMCP
from snodo.mcp.pr import PrMCP
from snodo.mcp.planner import PlannerMCP
from snodo.mcp.tools import TOOL_REGISTRY, MODE_TOOL_MAP, PLANNING_TOOLS, WORK_STARTING_TOOLS
from snodo.mcp.job_handlers import JobToolHandler
from snodo.mcp.model_handlers import ModelToolHandler
from snodo.mcp.decision_handlers import DecisionToolHandler
from snodo.mcp.recon_handlers import ReconToolHandler
from snodo.mcp.plan_handlers import PlanToolHandler

logger = logging.getLogger(__name__)


def _installed_version() -> str:
    """Read the distribution metadata without relying on the cached package."""
    try:
        return importlib.metadata.version("snodo")
    except importlib.metadata.PackageNotFoundError:
        from snodo.version import __version__
        return __version__


class MCPError(Exception):
    """MCP server error."""


class ProtocolMCPServer:
    """MCP server generated from a Protocol definition.

    Exposes the tools the protocol's active mode(s) grant. A tool call is
    never refused for want of a token the caller holds: the validator
    quorum is enforced inside the engine loop, per task (see ADR 047).
    """

    def __init__(
        self,
        protocol: Protocol,
        project_root: str,
        mode_id: Optional[str] = None,
        token_issuer: Optional[TokenIssuer] = None,
        audit_log: Any = None,
    ):
        """Initialize MCP server from protocol.

        Args:
            protocol: Protocol definition
            project_root: Project root directory
            mode_id: Specific mode to serve (None = all modes)
            token_issuer: Token issuer backing validate_task's single-use
                token (recorded on a pass, consumed at the dispatch boundary)
            audit_log: Optional AuditLog for INV4 event logging
        """
        self.protocol = protocol
        self.project_root = project_root
        self.mode_id = mode_id
        self._audit_log = audit_log
        from snodo.version import __version__
        self.serving_version = __version__
        self._stale_warning_emitted = False
        self._version_lock = threading.Lock()
        self.token_issuer = token_issuer or TokenIssuer(audit_log=audit_log)
        self._validation_token: Optional[ValidationToken] = None
        self._token_lock = threading.Lock()

        # Validation outcomes validate_task can set
        # (pass | escalate | blocker | validator_error; see ADR 015).
        self._validation_status: Optional[str] = None

        from snodo.engine.policy import PolicyEvaluator
        from snodo.infrastructure.decisions import VerifyOnlyDecisionRecordIssuer
        from snodo.infrastructure.signing_keys import load_public_key

        try:
            self._decision_issuer = VerifyOnlyDecisionRecordIssuer(
                load_public_key(), audit_log=audit_log
            )
        except Exception:
            self._decision_issuer = None
        self._policy_evaluator = PolicyEvaluator(
            decision_issuer=self._decision_issuer,
        )

        # Tools whose handlers may block the event loop — dispatched async
        self._SLOW_TOOLS = {"validate_task", "run_tests", "run_plan"}

        # Tools whose handlers already narrate their work and can accept a
        # per-call progress sink. run_plan uses it only for opt-in wait=true
        # calls; the default remains an immediate job submission.
        self._PROGRESS_TOOLS = {"validate_task", "run_plan"}

        # Initialize backing MCPs
        self.workspace = WorkspaceMCP(project_root)
        self.git = GitMCP(project_root)
        self.shell = ShellMCP(project_root)
        self.planner = PlannerMCP(project_root, audit_log=self._audit_log)

        # PrMCP with auto-detected provider
        provider = self._resolve_provider()
        self.pr = PrMCP(project_root, provider=provider)

        self._mcp_map = {
            "workspace": self.workspace,
            "git": self.git,
            "shell": self.shell,
            "pr": self.pr,
            "planner": self.planner,
        }
        self._job_handler = JobToolHandler(project_root, serving_version=self.serving_version)
        self._model_handler = ModelToolHandler()
        self._decision_handler = DecisionToolHandler(project_root)
        self._recon_handler = ReconToolHandler(project_root)
        self._tools = self._resolve_tools()

        self._core_handler = CoreToolHandler(self)
        self._plan_handler = PlanToolHandler(self)

        # Build registry of tool handlers, detecting collisions
        self._dispatch = {}
        handlers = [
            self._job_handler,
            self._model_handler,
            self._decision_handler,
            self._recon_handler,
            self._plan_handler,
            self._core_handler,
        ]
        for h in handlers:
            for tool_name, handler_fn in h.tool_handlers().items():
                if tool_name in self._dispatch:
                    raise ValueError(f"Duplicate tool handler registered for tool: {tool_name}")
                self._dispatch[tool_name] = handler_fn

    def _audit(self, event_type: str, data: Dict[str, Any]) -> None:
        """Log to injected audit log if available."""
        if self._audit_log is not None:
            self._audit_log.append_event(event_type, data)

    def _version_status(self) -> Optional[dict]:
        """Return a stale-install diagnostic, if the installed code moved on."""
        installed = _installed_version()
        if installed == self.serving_version:
            return None
        with self._version_lock:
            if not self._stale_warning_emitted:
                logger.warning(
                    "snodo server is stale: serving %s, installed %s; restart "
                    "the server when convenient",
                    self.serving_version,
                    installed,
                )
                self._stale_warning_emitted = True
        return {
            "serving": self.serving_version,
            "installed": installed,
            "stale": True,
        }

    def _decorate_result(self, result: Any) -> Any:
        """Make an installed-version change visible without changing outcomes."""
        status = self._version_status()
        if status is not None and isinstance(result, dict):
            return {**result, "snodo_staleness": status}
        return result

    @staticmethod
    def _args_hash(arguments: Dict[str, Any]) -> str:
        """Produce a truncated hash of tool arguments (no content leakage)."""
        raw = str(sorted(arguments.items())).encode()
        return hashlib.sha256(raw).hexdigest()[:16]

    def _active_mode(self) -> str:
        """Resolve the active mode for audit attribution.

        When this server is pinned to a single mode (``mode_id`` set), that mode
        is authoritative.  Otherwise the active mode is read from the persisted
        project state (``.snodo/state.json``), falling back to the protocol's
        initial mode.  This is what makes it possible to attribute an operation
        to a mode from the audit log alone — disjointness no longer provides
        mode inference under the relaxed WF1 (see ADR 017, carried forward by
        ADR 047).
        """
        if self.mode_id:
            return self.mode_id

        try:
            from snodo.infrastructure.state import read_state
            state = read_state(self.project_root)
            if state.current_mode and self.protocol.get_mode(state.current_mode):
                return state.current_mode
        except Exception as e:  # noqa: BLE001 — best-effort attribution
            logger.debug("Failed to read current mode from state: %s", e)

        return self.protocol.initial_mode

    def _resolve_provider(self) -> Optional[Any]:
        """Resolve code host provider from protocol metadata.

        Returns:
            CodeHostProvider instance, or None if detection fails
        """
        try:
            from snodo.providers.registry import detect_provider
            return detect_provider(
                self.project_root,
                protocol_metadata=self.protocol.metadata,
            )
        except Exception:
            return None

    def _resolve_tools(self) -> Dict[str, dict]:
        """Resolve available MCP tools from protocol modes.

        Returns:
            Dict of tool_name -> tool schema for all available tools.
        """
        tools: Dict[str, dict] = {}

        if self.mode_id:
            modes = [self.protocol.get_mode(self.mode_id)]
            if modes[0] is None:
                raise MCPError(f"Mode not found in protocol: {self.mode_id}")
        else:
            modes = list(self.protocol.modes)

        for mode in modes:
            for proto_tool in mode.tools:  # type: ignore[union-attr]
                concrete_names = MODE_TOOL_MAP.get(proto_tool, [])
                for name in concrete_names:
                    if name in TOOL_REGISTRY and name not in tools:
                        tools[name] = TOOL_REGISTRY[name]

        # Always include validate_task (meta-tool: runs the pre-execute
        # quorum; a pass records the single-use token dispatch consumes)
        tools["validate_task"] = TOOL_REGISTRY["validate_task"]

        # The planning surface is the human gate above the task loop: a
        # control-plane consumer driving the all-modes server must be able to
        # propose, validate and run plans, not only dispatch tasks. A server
        # pinned to one mode stays capability-filtered — its mode grants plan
        # tools only through the "plan" capability (MODE_TOOL_MAP).
        if self.mode_id is None:
            for name in PLANNING_TOOLS:
                if name not in tools:
                    tools[name] = TOOL_REGISTRY[name]

        # The planning surface and the job surface are one surface: a
        # server that can start work must be able to report on it. Enforced
        # here, after every grant is resolved, so the pairing cannot be
        # broken by a protocol whose modes never say "dispatch" (a real
        # project shipped a server that handed out a job_id and withheld
        # every tool that could ask after it — the orchestrator read
        # .snodo/jobs/<id>/ files off disk to cope). Only read-only
        # observers travel; no mutating tool is granted to a mode that does
        # not hold it.
        for starter, observers in WORK_STARTING_TOOLS.items():
            if starter in tools:
                for name in observers:
                    tools[name] = TOOL_REGISTRY[name]

        return tools

    def get_tools(self) -> List[dict]:
        """Return MCP tool list for tools/list response.

        Returns:
            List of tool descriptors with name, description, inputSchema.
        """
        result = []
        for name, schema in self._tools.items():
            result.append({
                "name": name,
                "description": schema["description"],
                "inputSchema": schema["inputSchema"],
            })
        return result

    def call_tool(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        progress_sink: Optional[Any] = None,
    ) -> Any:
        """Execute a tool call.

        Args:
            name: Tool name
            arguments: Tool arguments
            progress_sink: Optional narration callback (a single string). It
                is offered only to the handlers in ``_PROGRESS_TOOLS``, which
                already route existing progress through it; a handler's
                result is identical whether or not one is supplied. The sink
                is an observer: handlers wrap it so a failure in it cannot
                take the run down (snodo.engine.progress.ProgressSink).

        Returns:
            Tool result

        Raises:
            MCPError: If tool not found or execution fails
        """
        arguments = arguments or {}

        if name not in self._tools:
            raise MCPError(f"Unknown tool: {name}")

        schema = self._tools[name]

        self._audit("tool_call", {
            "op": "tool_call",
            "tool_name": name,
            "mode": self._active_mode(),
            "args_hash": self._args_hash(arguments),
        })

        handler = self._dispatch.get(name)
        if handler:
            # Check if the handler method has been replaced (e.g. mocked in tests)
            instance = getattr(handler, "__self__", None)
            func_name = getattr(handler, "__name__", None)
            if instance is not None and func_name is not None:
                current_attr = getattr(instance, func_name, None)
                if current_attr is not handler:
                    handler = current_attr
            if progress_sink is not None and name in self._PROGRESS_TOOLS:
                return self._decorate_result(handler(arguments, progress_sink=progress_sink))
            return self._decorate_result(handler(arguments))

        # Dispatch to backing MCP
        return self._decorate_result(self._dispatch_tool(name, schema, arguments))

    def is_slow_tool(self, name: str) -> bool:
        """Return True if *name* is a tool whose handler may block the event loop."""
        return name in self._SLOW_TOOLS

    def accepts_progress(self, name: str) -> bool:
        """Return True if *name*'s handler narrates work to a progress sink."""
        return name in self._PROGRESS_TOOLS

    async def call_tool_async(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        progress_sink: Optional[Any] = None,
    ) -> Any:
        """Async wrapper for slow tools — runs the blocking work in a thread.

        FastMCP natively awaits async tool functions, so the event loop
        stays free to serve other calls while the slow subprocess runs. The
        progress sink (if any) is handed to the handler and called from the
        worker thread; the transport's emitter marshals notifications back
        onto the event loop, so it works from whichever thread narrates.
        """
        return await asyncio.to_thread(
            functools.partial(self.call_tool, name, arguments, progress_sink)
        )

    def _dispatch_tool(self, name: str, schema: dict, arguments: dict) -> Any:
        """Dispatch a tool call to the backing MCP.

        Args:
            name: Tool name
            schema: Tool schema with mcp and method info
            arguments: Tool arguments

        Returns:
            Tool result

        Raises:
            MCPError: If MCP or method not found, or execution fails
        """
        mcp_name = schema["mcp"]
        method_name = schema["method"]
        mcp_instance = self._mcp_map.get(mcp_name)

        if not mcp_instance or not method_name:
            raise MCPError(f"No backing MCP for tool: {name}")

        method = getattr(mcp_instance, method_name, None)
        if not method:
            raise MCPError(f"Method {method_name} not found on {mcp_name} MCP")

        try:
            return method(**arguments)
        except Exception as e:
            raise MCPError(f"Tool execution failed: {e}") from e

    def _handle_validate_task(self, arguments: Dict[str, Any], progress_sink: Optional[Any] = None) -> dict:
        return self._core_handler.handle_validate_task(arguments, progress_sink=progress_sink)

    def _handle_dispatch_task(self, arguments: Dict[str, Any]) -> dict:
        return self._core_handler.handle_dispatch_task(arguments)

    def _handle_retry_job(self, arguments: Dict[str, Any]) -> dict:
        return self._core_handler.handle_retry_job(arguments)

class CoreToolHandler:
    """Handles validate_task, dispatch_task, and retry_job tool calls."""

    def __init__(self, server: "ProtocolMCPServer"):
        self.server = server

    def handle_validate_task(
        self,
        arguments: Dict[str, Any],
        progress_sink: Optional[Any] = None,
    ) -> dict:
        """Run the real validators and return one of four discriminated outcomes.

        The four validation outcomes are ``pass`` / ``escalate`` / ``blocker`` /
        ``validator_error`` — the engine's canonical vocabulary is five, but
        ``environment_error`` is an execution halt and is not reachable here
        (see ADR 015).
        A validation token is minted ONLY on ``pass`` (or on ``escalate`` after a
        human has adjudicated via ``snodo authorize`` and the agent re-calls).

        *progress_sink* (optional) receives the validator runner's existing
        narration (started / finished / per-turn lines). It changes nothing
        about the outcome, the token, or the response shape.
        """
        task_id = arguments.get("task_id")
        if not task_id:
            raise MCPError("validate_task requires task_id")
        task_spec = arguments.get("task_spec") or arguments.get("spec") or ""

        server = self.server
        protocol = server.protocol
        mode_id = server.mode_id or protocol.initial_mode

        from snodo.validators.runner import (
            classify_outcome,
            resolve_validator_completion,
            resolve_validators,
            run_validators,
        )
        from snodo.engine.policy import policy_decision_to_dict

        mode, validators = resolve_validators(protocol, mode_id, "pre_execute")

        if mode is None:
            server._validation_status = "validator_error"
            return self._outcome(
                "validator_error", task_id, [],
                "No active mode — cannot resolve pre-execute validators.",
            )

        results: list = []

        # 1. Resolve the validator LLM. Failure → validator_error (not a pass).
        try:
            completion_fn, validator_model, validator_config = resolve_validator_completion()
        except Exception as e:  # noqa: BLE001
            server._validation_status = "validator_error"
            return self._outcome(
                "validator_error", task_id,
                [{"validator_id": "config", "severity": "blocker",
                  "justification": f"Could not resolve validator LLM: {e}"}],
                "Could not resolve validator LLM — retry or inspect logs.",
            )

        # 2. Run the protocol's real validators via the shared engine runner.
        task = Task(id=task_id, spec=task_spec)
        decision_records = self._load_decision_records(mode_id)
        protocol_results, _ = run_validators(
            protocol=protocol,
            validators=validators,
            task=task,
            phase="pre_execute",
            completion_fn=completion_fn,
            default_model=validator_model,
            validator_config=validator_config,
            workspace_mcp=server.workspace,
            git_mcp=server.git,
            current_mode=mode_id,
            session_id="",
            audit_log=server._audit_log,
            progress_cb=progress_sink,
        )
        results.extend(protocol_results)

        # 3. Evaluate policy (shared with the engine) — no hand-rolled logic.
        decision = server._policy_evaluator.evaluate(
            results,
            protocol.disagreement_policy,
            "pre_execute",
            decision_records=decision_records if server._decision_issuer else None,
            task_ref=task_id,
        )

        status = classify_outcome(results, decision)
        server._validation_status = status

        serialized = [result_record(r) for r in results]

        server._audit("validator_results", {
            "op": "validator_results",
            "task_id": task_id,
            "status": status,
            "validator_outcomes": [
                {"validator_id": r.validator_id, "severity": r.severity}
                for r in results
            ],
        })

        if status == "pass":
            token = server.token_issuer.issue_token(
                task_id=task_id,
                validator_results=results,
                consensus=protocol.disagreement_policy.value,
            )
            if token:
                with server._token_lock:
                    server._validation_token = token
            return {
                "status": "pass",
                "token_issued": token is not None,
                "results": serialized,
                "instruction": "Validation passed. Call dispatch_task with the task spec.",
            }

        if status == "escalate":
            decision_id = self._persist_escalation(task_id, mode_id, results, decision)
            return {
                "status": "escalate",
                "token_issued": False,
                "decision_id": decision_id,
                "policy": protocol.disagreement_policy.value,
                "options": [
                    {**result_record(r), "decision": "proceed"}
                    for r in results if r.severity != "pass"
                ],
                "results": serialized,
                "policy_decision": policy_decision_to_dict(decision),
                "instruction": f"Human review required. Run: snodo authorize {decision_id}",
            }

        if status == "blocker":
            return self._outcome(
                "blocker", task_id, serialized,
                "Blockers present. Fix the code and re-validate; "
                "if exhausted, revise the spec.",
            )

        # validator_error
        return self._outcome(
            "validator_error", task_id, serialized,
            "A validator failed to produce a verdict. Retry or inspect logs.",
        )

    def _outcome(self, status: str, task_id: str, results: list, instruction: str) -> dict:
        """Build a no-token validation-outcome response."""
        return {
            "status": status,
            "token_issued": False,
            "task_id": task_id,
            "results": results,
            "instruction": instruction,
        }

    def _load_decision_records(self, mode_id: str) -> list:
        """Load signed DecisionRecords from the active session (for policy consultation)."""
        try:
            from snodo.infrastructure.state import read_state
            from snodo.infrastructure.session import SessionManager

            state = read_state(self.server.project_root)
            mode = state.current_mode or mode_id or self.server.protocol.initial_mode
            mgr = SessionManager()
            session = mgr.get_active_session(mode, self.server.project_root)
            if session is None:
                return []
            records = session.checkpoint.decisions.get("decision_records", [])
            if isinstance(records, list):
                return [r for r in records if isinstance(r, str)]
        except Exception as e:  # noqa: BLE001 — session read is best-effort
            logger.debug("Failed to read decision records from session: %s", e)
        return []

    def _persist_escalation(
        self, task_id: str, mode_id: str, results: list, decision: Any
    ) -> str:
        """Persist the escalation as a pending decision (engine shape) for authorize.

        Mirrors ``engine/nodes/writeback._auto_write_pending_decisions`` and
        ``decision_handlers.handle_propose_adjudicate``: an ``adjudicate`` entry is
        written to ``session.checkpoint.decisions["pending_decisions"]`` keyed by
        the task id (the ``decision_id``).
        """
        from datetime import datetime, timezone

        from snodo.engine.policy import policy_decision_to_dict
        from snodo.infrastructure.state import read_state
        from snodo.infrastructure.session import SessionManager

        try:
            state = read_state(self.server.project_root)
            mode = state.current_mode or mode_id or self.server.protocol.initial_mode
            mgr = SessionManager(audit_log=self.server._audit_log)
            session = mgr.get_active_session(mode, self.server.project_root)
            if session is None:
                return task_id

            pending = session.checkpoint.decisions.get("pending_decisions", {})
            if not isinstance(pending, dict):
                pending = {}

            now = datetime.now(timezone.utc).isoformat()
            for r in results:
                if r.severity not in ("warn", "blocker"):
                    continue
                entry = {
                    "type": "adjudicate",
                    "validator_id": r.validator_id,
                    "decision": "proceed",
                    "justification": r.justification,
                    "severity": r.severity,
                    "proposed_by": "mcp",
                    "timestamp": now,
                    "policy_decision": policy_decision_to_dict(decision),
                }
                pending[task_id] = entry

            mgr.update_decision(session.session_id, "pending_decisions", pending)
            self.server._audit("disagreement_escalated", {
                "op": "disagreement_escalated",
                "phase": "pre_execute",
                "task_ref": task_id,
                "policy": self.server.protocol.disagreement_policy.value,
                "decision_id": task_id,
            })
        except Exception as e:  # noqa: BLE001 — best-effort persistence
            logger.warning("Failed to persist disagreement escalation for %s: %s", task_id, e)
        return task_id

    def _guard_coder_available(self, coding_model: str) -> None:
        """Refuse dispatch when the coder about to be invoked cannot be invoked HERE.

        Readiness checks the configured coder's PATH in the operator's shell;
        this dispatch spawns the job as a child of *this* process, and the two
        environments differ — the check was made where it is not needed and
        not made where it is, which is how a task whose spec passed every
        validator died at execute on a missing binary. Resolve it where it
        bites: one ``shutil.which`` per declared requirement, before the job
        is submitted, before any post-dispatch validation cost is spent.
        Refusing is not a verdict on the task — the validation token stays
        unconsumed, so installing the program and dispatching again does not
        require re-validating a specification that never changed.
        """
        server = self.server
        protocol = server.protocol
        mode_id = server.mode_id or protocol.initial_mode
        mode_obj = protocol.get_mode(mode_id) if mode_id else None
        mode_coder = getattr(mode_obj, "coder", None) if mode_obj else None
        mode_coder_config = getattr(mode_obj, "coder_config", None) or {}

        from snodo.coders import check_coder_available, resolve_coder_name

        resolved_model = coding_model or mode_coder_config.get("model") or ""
        if not resolved_model:
            try:
                from snodo.infrastructure.config import load_llm_config
                resolved_model = load_llm_config().coder.model or ""
            except Exception:  # noqa: BLE001 — model resolution is best-effort here
                resolved_model = ""

        coder_name = resolve_coder_name(model=resolved_model, mode_coder=mode_coder)
        problem = check_coder_available(coder_name)
        if problem is None:
            return
        binary, remediation = problem
        server._audit("dispatch_refused_coder_unavailable", {
            "op": "dispatch_refused_coder_unavailable",
            "coder": coder_name,
            "binary": binary,
            "mode": server._active_mode(),
        })
        raise MCPError(
            f"dispatch_task refused: coder '{coder_name}' needs '{binary}' "
            f"on the PATH of the process that runs jobs, and it is not there. "
            f"{remediation} Install it and dispatch again — nothing about the "
            "task failed; the validation token is untouched."
        )

    def handle_dispatch_task(self, arguments: Dict[str, Any]) -> dict:
        """Submit a task spec to JobManager for background execution."""
        task_spec = arguments.get("task_spec")
        if not task_spec:
            raise MCPError("dispatch_task requires task_spec")
        coding_model = arguments.get("coding_model", "")

        # Establish in THIS process that the coder can be invoked at all
        # before a task is dispatched (the halt taxonomy calls the
        # after-the-fact version ``environment_error``; ADR 015). Refusing here
        # means the run never starts and no validation cost is spent on it.
        self._guard_coder_available(coding_model)

        from snodo.jobs import JobManager

        job_mgr = JobManager(self.server.project_root)
        task_args: Dict[str, Any] = {
            "description": task_spec,
            "cwd": self.server.project_root,
        }
        if coding_model:
            task_args["model"] = coding_model
        if self.server.mode_id:
            task_args["mode"] = self.server.mode_id

        job_id = job_mgr.submit(task_args)

        task_spec_hash = hashlib.sha256(task_spec.encode()).hexdigest()[:16]
        self.server._audit("dispatch_request", {
            "op": "dispatch_request",
            "task_spec_hash": task_spec_hash,
            "job_id": job_id,
            "mode": self.server._active_mode(),
        })

        # Single-use: consume the last recorded token at the dispatch
        # boundary. This is an audit link between a satisfied quorum and
        # the work dispatched, not a gate — dispatch proceeds either way
        # (the run re-runs the quorum per task inside the engine loop).
        # The INSERT is the claim — atomic across processes.
        with self.server._token_lock:
            token = self.server._validation_token
            if token is not None:
                try:
                    consumed = self.server.token_issuer.consume_token(token)
                except TokenStoreError as e:
                    raise MCPError(
                        f"dispatch_task failed: token store unavailable: {e}"
                    ) from e
                self.server._validation_token = None
            else:
                consumed = False
        if consumed:
            self.server._audit("token_consumed", {
                "op": "token_consumed",
                "task_spec_hash": task_spec_hash,
            })

        result = {
            "status": "accepted",
            "task_id": job_id,
            "task_spec": task_spec,
        }
        if coding_model:
            result["coding_model"] = coding_model
        return result

    def handle_retry_job(self, arguments: Dict[str, Any]) -> dict:
        """Look up task_id from a failed job and dispatch a retry.

        Three shapes, matching ``snodo run --retry``: no spec argument keeps the
        recorded spec (the default, and what an operational failure wants),
        ``append_spec`` adds guidance on top of it, and ``revised_spec`` replaces
        it — the only shape that discards anything, so the spec it discards is
        audited as ``spec_replaced`` to stay recoverable.
        """
        from snodo.jobs import JobManager

        job_id = arguments.get("job_id", "")
        if not job_id:
            raise MCPError("retry_job requires job_id")

        revised_spec = (arguments.get("revised_spec") or "").strip()
        append_spec = (arguments.get("append_spec") or "").strip()
        if revised_spec and append_spec:
            raise MCPError(
                "retry_job takes either revised_spec (which replaces the recorded "
                "spec) or append_spec (which adds guidance on top of it), not both"
            )

        job_mgr = JobManager(self.server.project_root)
        job_dir = job_mgr._job_dir(job_id)

        import json
        task_path = job_dir / "task.json"
        if not task_path.exists():
            raise MCPError(f"No task.json found for job {job_id}")

        try:
            with open(task_path) as f:
                task_data = json.load(f)
        except Exception as e:
            raise MCPError(f"Error reading task.json: {e}") from e

        if task_data.get("plan_name"):
            # A plan run is not a task and has no task spec to re-dispatch.
            # Retrying it would re-run an entire plan under a retry path that
            # means "the same task's spec, again" — start a fresh plan run
            # instead (run_plan), which is a deliberate whole-plan action.
            raise MCPError(
                f"Job {job_id} is a plan run, not a task; retry_job re-dispatches "
                f"a task. Start the plan again with run_plan."
            )

        task_id = task_data.get("task_id", "")
        original_spec = task_data.get("description", "")

        if revised_spec and same_spec(revised_spec, original_spec):
            # Handing back the recorded spec changes nothing; do not book it as
            # a replacement (which would report a spec as superseded that was
            # never lost).
            revised_spec = ""

        if revised_spec:
            description = revised_spec
            self.server._audit("spec_replaced", {
                "op": "spec_replaced",
                "task_ref": task_id,
                "previous_spec": original_spec,
                "new_spec": revised_spec,
            })
        elif append_spec:
            description = spec_with_guidance(original_spec, append_spec)
        else:
            description = original_spec

        task_args: Dict[str, Any] = {
            "description": description,
            "cwd": self.server.project_root,
            "retry_task_id": task_id,
        }
        if self.server.mode_id:
            task_args["mode"] = self.server.mode_id

        new_job_id = job_mgr.submit(task_args)

        return {
            "status": "accepted",
            "job_id": new_job_id,
            "task_id": task_id,
            "description": description,
            "spec_action": (
                "replaced" if revised_spec
                else "appended" if append_spec
                else "unchanged"
            ),
        }

    def tool_handlers(self) -> dict:
        return {
            "validate_task": self.server._handle_validate_task,
            "dispatch_task": self.server._handle_dispatch_task,
            "retry_job": self.server._handle_retry_job,
        }
