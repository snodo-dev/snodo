"""Protocol-Driven MCP Server.

Generates an MCP server from a Protocol definition:
- Maps protocol mode tools to real MCP implementations (workspace, git, shell)
- Enforces WF1: tool execution requires a valid validation token
- Filters available tools by active mode

Transport is handled by FastMCP (see transport.py).
"""

import asyncio
import hashlib
import threading
from typing import Any, Dict, List, Optional

from snodo.compiler.models import Protocol
from snodo.infrastructure.tokens import TokenIssuer, TokenStoreError, ValidationToken
from snodo.core.interfaces import Task, ValidatorResult
from snodo.tools.workspace import WorkspaceMCP
from snodo.tools.git import GitMCP
from snodo.tools.shell import ShellMCP
from snodo.mcp.pr import PrMCP
from snodo.mcp.planner import PlannerMCP
from snodo.mcp.tools import TOOL_REGISTRY, MODE_TOOL_MAP
from snodo.mcp.job_handlers import JobToolHandler
from snodo.mcp.model_handlers import ModelToolHandler
from snodo.mcp.decision_handlers import DecisionToolHandler
from snodo.mcp.recon_handlers import ReconToolHandler


class MCPError(Exception):
    """MCP server error."""


class ProtocolMCPServer:
    """MCP server generated from a Protocol definition.

    Exposes tools filtered by protocol mode and enforces WF1:
    write/mutating tools require a valid validation token.
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
            token_issuer: Token issuer for WF1 enforcement
            audit_log: Optional AuditLog for INV4 event logging
        """
        self.protocol = protocol
        self.project_root = project_root
        self.mode_id = mode_id
        self._audit_log = audit_log
        self.token_issuer = token_issuer or TokenIssuer(audit_log=audit_log)
        self._validation_token: Optional[ValidationToken] = None
        self._token_lock = threading.Lock()

        # Four-outcome validate_task state (pass | escalate | blocker | validator_error)
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
        self._SLOW_TOOLS = {"validate_task", "run_tests"}

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
        self._job_handler = JobToolHandler(project_root)
        self._model_handler = ModelToolHandler()
        self._decision_handler = DecisionToolHandler(project_root)
        self._recon_handler = ReconToolHandler(project_root)
        self._tools = self._resolve_tools()

        self._core_handler = CoreToolHandler(self)

        # Build registry of tool handlers, detecting collisions
        self._dispatch = {}
        handlers = [
            self._job_handler,
            self._model_handler,
            self._decision_handler,
            self._recon_handler,
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

    @staticmethod
    def _args_hash(arguments: Dict[str, Any]) -> str:
        """Produce a truncated hash of tool arguments (no content leakage)."""
        raw = str(sorted(arguments.items())).encode()
        return hashlib.sha256(raw).hexdigest()[:16]

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

        # Always include validate_task (meta-tool for WF1 token issuance)
        tools["validate_task"] = TOOL_REGISTRY["validate_task"]

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

    def call_tool(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Execute a tool call with WF1 enforcement.

        Args:
            name: Tool name
            arguments: Tool arguments

        Returns:
            Tool result

        Raises:
            MCPError: If tool not found, token invalid, or execution fails
        """
        arguments = arguments or {}

        if name not in self._tools:
            raise MCPError(f"Unknown tool: {name}")

        schema = self._tools[name]
        self._enforce_wf1(name, schema)

        self._audit("tool_call", {
            "op": "tool_call",
            "tool_name": name,
            "mode": self.mode_id or "all",
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
                    return current_attr(arguments)
            return handler(arguments)

        # Dispatch to backing MCP
        return self._dispatch_tool(name, schema, arguments)

    def is_slow_tool(self, name: str) -> bool:
        """Return True if *name* is a tool whose handler may block the event loop."""
        return name in self._SLOW_TOOLS

    async def call_tool_async(self, name: str, arguments: Optional[Dict[str, Any]] = None) -> Any:
        """Async wrapper for slow tools — runs the blocking work in a thread.

        FastMCP natively awaits async tool functions, so the event loop
        stays free to serve other calls while the slow subprocess runs.
        """
        return await asyncio.to_thread(self.call_tool, name, arguments)

    def _enforce_wf1(self, name: str, schema: dict) -> None:
        """Enforce WF1: mutating tools require a valid validation token.

        This is also the INV3 enforcement point: mutations are gated
        behind a valid token, which can only be issued by a satisfied
        validator quorum.  The token cannot be forged (INV1/JWT), the
        quorum cannot be bypassed (WF1 checks the token is present),
        so non-overridable validation is structurally enforced here.

        Args:
            name: Tool name (for error messages)
            schema: Tool schema with requires_token flag

        Raises:
            MCPError: If token is missing or invalid
        """
        if not schema["requires_token"]:
            return
        with self._token_lock:
            if not self._validation_token:
                status = getattr(self, "_validation_status", None) or "none"
                self._audit("wf1_violation", {
                    "op": "wf1_violation",
                    "tool": name,
                    "mode": self.mode_id or "all",
                    "reason": "no_token",
                })
                raise MCPError(
                    f"WF1 violation: tool '{name}' requires a validation token. "
                    f"validate_task last returned status='{status}' — a token is "
                    f"only issued on status='pass'. Call validate_task first."
                )
            try:
                valid = self.token_issuer.verify_token(self._validation_token)
            except TokenStoreError as e:
                self._audit("wf1_violation", {
                    "op": "wf1_violation",
                    "tool": name,
                    "mode": self.mode_id or "all",
                    "reason": "token_store_unavailable",
                })
                raise MCPError(
                    f"WF1 violation: cannot verify validation token for tool "
                    f"'{name}' — token store unavailable: {e}"
                )
            if not valid:
                self._audit("wf1_violation", {
                    "op": "wf1_violation",
                    "tool": name,
                    "mode": self.mode_id or "all",
                    "reason": "invalid_token",
                })
                raise MCPError(
                    f"WF1 violation: invalid or expired validation token for tool '{name}'"
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
            raise MCPError(f"Tool execution failed: {e}")

    def _handle_validate_task(self, arguments: Dict[str, Any]) -> dict:
        return self._core_handler.handle_validate_task(arguments)

    def _handle_dispatch_task(self, arguments: Dict[str, Any]) -> dict:
        return self._core_handler.handle_dispatch_task(arguments)

    def _handle_retry_job(self, arguments: Dict[str, Any]) -> dict:
        return self._core_handler.handle_retry_job(arguments)

class CoreToolHandler:
    """Handles validate_task, dispatch_task, and retry_job tool calls."""

    def __init__(self, server: "ProtocolMCPServer"):
        self.server = server

    def handle_validate_task(self, arguments: Dict[str, Any]) -> dict:
        """Run the real validators and return one of four discriminated outcomes.

        ``pass`` / ``escalate`` / ``blocker`` / ``validator_error`` — see ADR 015.
        A validation token is minted ONLY on ``pass`` (or on ``escalate`` after a
        human has adjudicated via ``snodo authorize`` and the agent re-calls).
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

        # 1. pytest run — one validator result. Blockers are NOT downgraded.
        try:
            results.append(server.shell.run_tests("tests/", command_type="pytest"))
        except Exception as e:  # noqa: BLE001 — test runner crash is an error
            results.append(ValidatorResult(
                validator_id="test_runner",
                severity="blocker",
                justification=f"Test execution failed: {e}",
                error=True,
            ))

        # 2. Resolve the validator LLM. Failure → validator_error (not a pass).
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

        # 3. Run the protocol's real validators via the shared engine runner.
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
        )
        results.extend(protocol_results)

        # 4. Evaluate policy (shared with the engine) — no hand-rolled logic.
        decision = server._policy_evaluator.evaluate(
            results,
            protocol.disagreement_policy,
            decision_records=decision_records if server._decision_issuer else None,
            task_ref=task_id,
        )

        status = classify_outcome(results, decision)
        server._validation_status = status

        serialized = [
            {"validator_id": r.validator_id, "severity": r.severity,
             "justification": r.justification}
            for r in results
        ]

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
                    {"validator_id": r.validator_id, "severity": r.severity,
                     "justification": r.justification, "decision": "proceed"}
                    for r in results if r.severity != "pass"
                ],
                "results": serialized,
                "policy_decision": policy_decision_to_dict(decision),
                "instruction": f"Human review required. Run: snodo authorize {decision_id}",
            }

        if status == "blocker":
            return self._outcome(
                "blocker", task_id, serialized,
                "Blockers present. Fix the code and re-validate; if exhausted, revise the spec.",
            )

        # validator_error
        return self._outcome(
            "validator_error", task_id, serialized,
            "A validator failed to produce a verdict. Retry or inspect logs.",
        )

    def _outcome(self, status: str, task_id: str, results: list, instruction: str) -> dict:
        """Build a no-token four-outcome response."""
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
        except Exception:  # noqa: BLE001 — session read is best-effort
            pass
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
                pending[task_id] = {
                    "type": "adjudicate",
                    "validator_id": r.validator_id,
                    "decision": "proceed",
                    "justification": r.justification,
                    "severity": r.severity,
                    "proposed_by": "mcp",
                    "timestamp": now,
                    "policy_decision": policy_decision_to_dict(decision),
                }

            mgr.update_decision(session.session_id, "pending_decisions", pending)
            self.server._audit("disagreement_escalated", {
                "op": "disagreement_escalated",
                "phase": "pre_execute",
                "task_ref": task_id,
                "policy": self.server.protocol.disagreement_policy.value,
                "decision_id": task_id,
            })
        except Exception:  # noqa: BLE001 — best-effort persistence
            pass
        return task_id

    def handle_dispatch_task(self, arguments: Dict[str, Any]) -> dict:
        """Submit a task spec to JobManager for background execution."""
        task_spec = arguments.get("task_spec")
        if not task_spec:
            raise MCPError("dispatch_task requires task_spec")
        coding_model = arguments.get("coding_model", "")

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
            "mode": self.server.mode_id or "all",
        })

        # Single-use: consume the token at the dispatch boundary (the point
        # where the token authorises irreversible work). The INSERT is the
        # claim — atomic across processes. Fail closed if the store is down.
        with self.server._token_lock:
            token = self.server._validation_token
            if token is not None:
                try:
                    consumed = self.server.token_issuer.consume_token(token)
                except TokenStoreError as e:
                    raise MCPError(
                        f"dispatch_task failed: token store unavailable: {e}"
                    )
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
        """Look up task_id from a failed job and dispatch a retry."""
        from snodo.jobs import JobManager

        job_id = arguments.get("job_id", "")
        if not job_id:
            raise MCPError("retry_job requires job_id")

        revised_spec = arguments.get("revised_spec", "")

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
            raise MCPError(f"Error reading task.json: {e}")

        task_id = task_data.get("task_id", "")
        original_spec = task_data.get("description", "")

        description = revised_spec or original_spec
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
        }

    def tool_handlers(self) -> dict:
        return {
            "validate_task": self.server._handle_validate_task,
            "dispatch_task": self.server._handle_dispatch_task,
            "retry_job": self.server._handle_retry_job,
        }
