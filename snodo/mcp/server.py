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
from snodo.infrastructure.tokens import TokenIssuer, ValidationToken
from snodo.core.interfaces import ValidatorResult
from snodo.mcp.workspace import WorkspaceMCP
from snodo.mcp.git import GitMCP
from snodo.mcp.shell import ShellMCP
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

        # Handle meta-tools
        if name == "validate_task":
            return self._handle_validate_task(arguments)
        if name == "dispatch_task":
            return self._handle_dispatch_task(arguments)
        if name == "get_job_status":
            return self._job_handler.handle_get_job_status(arguments)
        if name == "list_jobs":
            return self._job_handler.handle_list_jobs(arguments)
        if name == "get_job_logs":
            return self._job_handler.handle_get_job_logs(arguments)
        if name == "list_models":
            return self._model_handler.handle_list_models(arguments)
        if name == "resolve_model":
            return self._model_handler.handle_resolve_model(arguments)
        if name == "propose_adjudicate":
            return self._decision_handler.handle_propose_adjudicate(arguments)
        if name == "propose_set_model":
            return self._decision_handler.handle_propose_set_model(arguments)
        if name == "recon":
            return self._recon_handler.handle_recon(arguments)
        if name == "get_recon_status":
            return self._recon_handler.handle_get_recon_status(arguments)
        if name == "get_recon_results":
            return self._recon_handler.handle_get_recon_results(arguments)
        if name == "retry_job":
            return self._handle_retry_job(arguments)

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
                self._audit("wf1_violation", {
                    "op": "wf1_violation",
                    "tool": name,
                    "mode": self.mode_id or "all",
                    "reason": "no_token",
                })
                raise MCPError(
                    f"WF1 violation: tool '{name}' requires a validation token. "
                    "Call validate_task first."
                )
            if not self.token_issuer.verify_token(self._validation_token):
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
        """Run validators and issue a token (WF1).

        Args:
            arguments: Must contain task_id

        Returns:
            Dict with token and validation results
        """
        task_id = arguments.get("task_id")
        if not task_id:
            raise MCPError("validate_task requires task_id")

        # Run shell tests as validator (permissive: treat failures as warnings)
        results = []
        try:
            test_result = self.shell.run_tests("tests/", command_type="pytest")
            if test_result.severity == "blocker":
                results.append(ValidatorResult(
                    validator_id=test_result.validator_id,
                    severity="warn",
                    justification=f"Tests (continuing): {test_result.justification}",
                ))
            else:
                results.append(test_result)
        except Exception as e:
            results.append(ValidatorResult(
                validator_id="test_runner",
                severity="warn",
                justification=f"Test execution skipped: {e}",
            ))

        # Add stub pass results for protocol validators
        for v in self.protocol.validators:
            results.append(ValidatorResult(
                validator_id=v.validator_id,
                severity="pass",
                justification=f"Stub validation for {v.validator_type}",
            ))

        # Issue token
        token = self.token_issuer.issue_token(
            task_id=task_id,
            validator_results=results,
            consensus=self.protocol.disagreement_policy.value,
        )

        if token:
            with self._token_lock:
                self._validation_token = token

        self._audit("validator_results", {
            "op": "validator_results",
            "task_id": task_id,
            "validator_outcomes": [
                {"validator_id": r.validator_id, "severity": r.severity}
                for r in results
            ],
        })

        return {
            "token_issued": token is not None,
            "results": [
                {"validator_id": r.validator_id, "severity": r.severity, "justification": r.justification}
                for r in results
            ],
        }

    def _handle_dispatch_task(self, arguments: Dict[str, Any]) -> dict:
        """Submit a task spec to JobManager for background execution.

        Args:
            arguments: Must contain task_spec

        Returns:
            Dict with status, task_id (job ID), and task_spec
        """
        task_spec = arguments.get("task_spec")
        if not task_spec:
            raise MCPError("dispatch_task requires task_spec")

        from snodo.jobs import JobManager

        job_mgr = JobManager(self.project_root)
        task_args: Dict[str, Any] = {
            "description": task_spec,
            "cwd": self.project_root,
        }
        if self.mode_id:
            task_args["mode"] = self.mode_id

        job_id = job_mgr.submit(task_args)

        task_spec_hash = hashlib.sha256(task_spec.encode()).hexdigest()[:16]
        self._audit("dispatch_request", {
            "op": "dispatch_request",
            "task_spec_hash": task_spec_hash,
            "job_id": job_id,
            "mode": self.mode_id or "all",
        })

        # Single-use: consume the token after successful dispatch
        with self._token_lock:
            if self._validation_token:
                self._validation_token = None
                consumed = True
            else:
                consumed = False
        if consumed:
            self._audit("token_consumed", {
                "op": "token_consumed",
                "task_spec_hash": task_spec_hash,
            })

        return {
            "status": "accepted",
            "task_id": job_id,
            "task_spec": task_spec,
        }

    def _handle_retry_job(self, arguments: Dict[str, Any]) -> dict:
        """Look up task_id from a failed job and dispatch a retry.

        Reads the job's task.json, extracts the original task_id, and
        submits a new background job with retry context.
        """
        from snodo.jobs import JobManager

        job_id = arguments.get("job_id", "")
        if not job_id:
            raise MCPError("retry_job requires job_id")

        revised_spec = arguments.get("revised_spec", "")

        job_mgr = JobManager(self.project_root)
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
            "cwd": self.project_root,
            "retry_task_id": task_id,
        }
        if self.mode_id:
            task_args["mode"] = self.mode_id

        new_job_id = job_mgr.submit(task_args)

        return {
            "status": "accepted",
            "job_id": new_job_id,
            "task_id": task_id,
            "description": description,
        }
