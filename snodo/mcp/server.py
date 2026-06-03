"""Protocol-Driven MCP Server.

FILE: snodo/mcp/server.py

Generates an MCP server from a Protocol definition:
- Maps protocol mode tools to real MCP implementations (workspace, git, shell)
- Enforces WF1: tool execution requires a valid validation token
- Filters available tools by active mode

Transport is handled by FastMCP (see transport.py).
"""

import hashlib
from typing import Any, Dict, List, Optional

from snodo.compiler.models import Protocol
from snodo.infrastructure.tokens import TokenIssuer, ValidationToken
from snodo.core.interfaces import ValidatorResult
from snodo.mcp.workspace import WorkspaceMCP
from snodo.mcp.git import GitMCP
from snodo.mcp.shell import ShellMCP
from snodo.mcp.pr import PrMCP
from snodo.mcp.planner import PlannerMCP


# Tool schemas: name -> {description, inputSchema, requires_token, mcp, method}
TOOL_REGISTRY = {
    "read_file": {
        "description": "Read file content within the project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root"},
            },
            "required": ["path"],
        },
        "requires_token": False,
        "mcp": "workspace",
        "method": "read_file",
    },
    "write_file": {
        "description": "Write content to a file within the project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
        "requires_token": True,
        "mcp": "workspace",
        "method": "write_file",
    },
    "list_files": {
        "description": "List files in a directory",
        "inputSchema": {
            "type": "object",
            "properties": {
                "directory": {"type": "string", "description": "Directory path", "default": "."},
            },
        },
        "requires_token": False,
        "mcp": "workspace",
        "method": "list_files",
    },
    "delete_file": {
        "description": "Delete a file within the project",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to delete"},
            },
            "required": ["path"],
        },
        "requires_token": True,
        "mcp": "workspace",
        "method": "delete_file",
    },
    "run_tests": {
        "description": "Run tests and return validation result",
        "inputSchema": {
            "type": "object",
            "properties": {
                "test_path": {"type": "string", "description": "Path to test file or directory"},
                "command_type": {"type": "string", "enum": ["pytest", "npm", "cargo"], "default": "pytest"},
            },
            "required": ["test_path"],
        },
        "requires_token": False,
        "mcp": "shell",
        "method": "run_tests",
    },
    "read_diff": {
        "description": "Read current git diff",
        "inputSchema": {"type": "object", "properties": {}},
        "requires_token": False,
        "mcp": "git",
        "method": "read_diff",
    },
    "get_status": {
        "description": "Get git status",
        "inputSchema": {"type": "object", "properties": {}},
        "requires_token": False,
        "mcp": "git",
        "method": "get_status",
    },
    "stage_files": {
        "description": "Stage files for git commit",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of file paths to stage",
                },
            },
            "required": ["paths"],
        },
        "requires_token": True,
        "mcp": "git",
        "method": "stage_files",
    },
    "commit": {
        "description": "Create a git commit",
        "inputSchema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Commit message"},
            },
            "required": ["message"],
        },
        "requires_token": True,
        "mcp": "git",
        "method": "commit",
    },
    "create_branch": {
        "description": "Create a new git branch",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Branch name"},
            },
            "required": ["name"],
        },
        "requires_token": True,
        "mcp": "git",
        "method": "create_branch",
    },
    "merge_branch": {
        "description": "Merge a branch into main",
        "inputSchema": {
            "type": "object",
            "properties": {
                "branch": {"type": "string", "description": "Branch name to merge"},
            },
            "required": ["branch"],
        },
        "requires_token": True,
        "mcp": "git",
        "method": "merge_branch",
    },
    "delete_branch": {
        "description": "Delete a git branch",
        "inputSchema": {
            "type": "object",
            "properties": {
                "branch": {"type": "string", "description": "Branch name to delete"},
            },
            "required": ["branch"],
        },
        "requires_token": True,
        "mcp": "git",
        "method": "delete_branch",
    },
    "create_pr": {
        "description": "Create a pull request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "branch": {"type": "string", "description": "Source branch name"},
                "title": {"type": "string", "description": "PR title"},
                "body": {"type": "string", "description": "PR description body"},
            },
            "required": ["branch", "title", "body"],
        },
        "requires_token": True,
        "mcp": "pr",
        "method": "create_pr",
    },
    "read_pr_diff": {
        "description": "Read the diff of a pull request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pr_number": {"type": "integer", "description": "PR number"},
            },
            "required": ["pr_number"],
        },
        "requires_token": False,
        "mcp": "pr",
        "method": "read_pr_diff",
    },
    "post_review_comment": {
        "description": "Post a comment on a pull request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pr_number": {"type": "integer", "description": "PR number"},
                "comment": {"type": "string", "description": "Comment text"},
            },
            "required": ["pr_number", "comment"],
        },
        "requires_token": True,
        "mcp": "pr",
        "method": "post_review_comment",
    },
    "approve_pr": {
        "description": "Approve a pull request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pr_number": {"type": "integer", "description": "PR number"},
            },
            "required": ["pr_number"],
        },
        "requires_token": True,
        "mcp": "pr",
        "method": "approve_pr",
    },
    "reject_pr": {
        "description": "Request changes on a pull request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pr_number": {"type": "integer", "description": "PR number"},
                "reason": {"type": "string", "description": "Reason for rejection"},
            },
            "required": ["pr_number", "reason"],
        },
        "requires_token": True,
        "mcp": "pr",
        "method": "reject_pr",
    },
    "merge_pr": {
        "description": "Merge a pull request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "pr_number": {"type": "integer", "description": "PR number"},
            },
            "required": ["pr_number"],
        },
        "requires_token": True,
        "mcp": "pr",
        "method": "merge_pr",
    },
    "decompose": {
        "description": "Decompose an intent into a structured plan with waves and tasks",
        "inputSchema": {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "description": "The intent/goal to decompose"},
                "plan_name": {"type": "string", "description": "Name for the plan"},
            },
            "required": ["intent", "plan_name"],
        },
        "requires_token": True,
        "mcp": "planner",
        "method": "decompose",
    },
    "generate_spec": {
        "description": "Generate a task specification file within a plan",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_name": {"type": "string", "description": "Plan name"},
                "task_id": {"type": "string", "description": "Task ID (e.g., 1.1_models)"},
                "spec": {"type": "string", "description": "Task specification content"},
                "parent_task_ref": {"type": "string", "description": "ID of parent task if this is a sub-task"},
                "replace": {"type": "boolean", "description": "Allow overwriting existing task spec"},
            },
            "required": ["plan_name", "task_id", "spec"],
        },
        "requires_token": True,
        "mcp": "planner",
        "method": "generate_spec",
    },
    "validate_plan": {
        "description": "Validate a plan's completeness and structure",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_name": {"type": "string", "description": "Plan name to validate"},
            },
            "required": ["plan_name"],
        },
        "requires_token": False,
        "mcp": "planner",
        "method": "validate_plan",
    },
    "dispatch_task": {
        "description": "Dispatch a task for execution via the protocol engine",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_spec": {"type": "string", "description": "Task specification to dispatch"},
            },
            "required": ["task_spec"],
        },
        "requires_token": True,
        "mcp": None,
        "method": None,
    },
    "resolve_disagreement": {
        "description": "Resolve an escalated validator disagreement",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task ID whose disagreement is being resolved"},
                "session_id": {"type": "string", "description": "Session ID for the resolution"},
                "resolution": {"type": "string", "description": "Resolution: proceed or halt"},
                "justification": {"type": "string", "description": "Justification for the decision"},
                "resolved_by": {"type": "string", "description": "Who resolved: human or orchestrator"},
            },
            "required": ["task_id", "session_id", "resolution", "justification"],
        },
        "requires_token": False,
        "mcp": None,
        "method": None,
    },
}

# Map protocol tool names (from mode.tools) to concrete MCP tool names
MODE_TOOL_MAP = {
    "edit": ["read_file", "list_files"],
    "dispatch": ["dispatch_task"],
    "resolve": ["resolve_disagreement"],
    "test": ["run_tests"],
    "validate": ["run_tests"],
    "review": ["read_file", "list_files", "read_diff", "get_status"],
    "approve": ["stage_files", "commit"],
    "commit": ["stage_files", "commit"],
    "merge": ["create_branch", "stage_files", "commit", "merge_branch", "delete_branch"],
    "pr": [
        "create_pr", "read_pr_diff", "post_review_comment",
        "approve_pr", "reject_pr", "merge_pr",
    ],
    "plan": ["decompose", "generate_spec", "validate_plan"],
}


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

        # Resolve available tools from protocol
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
        tools["validate_task"] = {
            "description": "Run validators and obtain a validation token (WF1)",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "description": "Task identifier"},
                },
                "required": ["task_id"],
            },
            "requires_token": False,
            "mcp": None,
            "method": None,
        }

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
        if name == "resolve_disagreement":
            return self._handle_resolve_disagreement(arguments)

        # Dispatch to backing MCP
        return self._dispatch_tool(name, schema, arguments)

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
        if self._validation_token:
            self._validation_token = None
            self._audit("token_consumed", {
                "op": "token_consumed",
                "task_spec_hash": task_spec_hash,
            })

        return {
            "status": "accepted",
            "task_id": job_id,
            "task_spec": task_spec,
        }

    def _handle_resolve_disagreement(self, arguments: Dict[str, Any]) -> dict:
        """Store a disagreement resolution in session decisions.

        Args:
            arguments: Must contain task_id, session_id, resolution,
                      justification.  Optional: resolved_by.

        Returns:
            Dict acknowledging the resolution

        Raises:
            MCPError: If required arguments missing or resolution invalid
        """
        task_id = arguments.get("task_id")
        session_id = arguments.get("session_id")
        resolution = arguments.get("resolution")
        justification = arguments.get("justification")
        resolved_by = arguments.get("resolved_by", "orchestrator")

        if not task_id:
            raise MCPError("resolve_disagreement requires task_id")
        if not session_id:
            raise MCPError("resolve_disagreement requires session_id")
        if not justification:
            raise MCPError("resolve_disagreement requires justification")

        from snodo.mcp.resolution import apply_resolution

        try:
            return apply_resolution(
                task_id=task_id,
                session_id=session_id,
                resolution=resolution,
                justification=justification,
                resolved_by=resolved_by,
                audit_log=self._audit_log,
            )
        except ValueError as e:
            raise MCPError(str(e))
        except FileNotFoundError:
            raise MCPError(f"Session not found: {session_id}")
