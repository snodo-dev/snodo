"""MCP tool schemas and mode-to-tool mappings.

Extracted from mcp/server.py to isolate static tool definitions.
"""

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
    "get_job_status": {
        "description": (
            "Poll execution status of a dispatched job. Call after "
            "dispatch_task returns a task_id. Status progresses: queued → "
            "running → completed | failed. Check for completed + exit_code=0 "
            "to confirm success."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID returned by dispatch_task"},
            },
            "required": ["job_id"],
        },
        "requires_token": False,
        "mcp": None,
        "method": None,
    },
    "list_jobs": {
        "description": "List all jobs for this project with their current status.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
        "requires_token": False,
        "mcp": None,
        "method": None,
    },
    "get_job_logs": {
        "description": (
            "Fetch stdout or stderr logs for a job. stream='stdout' or "
            "'stderr', tail=N for last N lines."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID returned by dispatch_task"},
                "stream": {"type": "string", "description": "stdout or stderr", "default": "stdout"},
                "tail": {"type": "integer", "description": "Return only the last N lines", "default": 50},
            },
            "required": ["job_id"],
        },
        "requires_token": False,
        "mcp": None,
        "method": None,
    },
    "validate_task": {
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
    },
    "propose_adjudicate": {
        "description": (
            "Propose a decision to override a validator concern. The "
            "human runs 'snodo authorize <task_id>' to review and sign. "
            "The agent cannot self-authorize — only the human CLI holds "
            "the signing key."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task identifier to adjudicate",
                },
                "validator_id": {
                    "type": "string",
                    "description": "Validator to override (e.g. 'security')",
                },
                "decision": {
                    "type": "string",
                    "description": "proceed or halt",
                },
                "justification": {
                    "type": "string",
                    "description": "Agent's justification for the proposed decision",
                },
            },
            "required": ["task_id", "validator_id", "decision", "justification"],
        },
        "requires_token": False,
        "mcp": None,
        "method": None,
    },
    "propose_set_model": {
        "description": (
            "Propose a model change (validator or coder). The human runs "
            "'snodo authorize <task_id>' to review and sign."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": "Task identifier this proposal is scoped to",
                },
                "proposed_model": {
                    "type": "string",
                    "description": "Model identifier (e.g. 'gemini/gemini-2.0-flash-exp')",
                },
                "scope": {
                    "type": "string",
                    "description": "Where to apply: 'coder' or 'validator:<id>'",
                },
                "justification": {
                    "type": "string",
                    "description": "Why the model change is needed",
                },
            },
            "required": ["task_id", "proposed_model", "scope", "justification"],
        },
        "requires_token": False,
        "mcp": None,
        "method": None,
    },
    "list_models": {
        "description": "List available models across configured providers",
        "inputSchema": {
            "type": "object",
            "properties": {
                "provider": {
                    "type": "string",
                    "description": "Optional: filter to a single provider (anthropic, openai, openrouter, google)",
                },
            },
        },
        "requires_token": False,
        "mcp": None,
        "method": None,
    },
    "resolve_model": {
        "description": (
            "Resolve a model query (e.g. \"sonnet\", \"gpt4o\", \"gemini\") "
            "to a concrete model. Returns exact match, or ambiguous candidates "
            "to pick from by index, or not_found."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Model query string, e.g. \"sonnet\", \"gemini-2.0\"",
                },
                "index": {
                    "type": "integer",
                    "description": "When ambiguous, resolve to the candidate at this index",
                },
            },
            "required": ["query"],
        },
        "requires_token": False,
        "mcp": None,
        "method": None,
    },
    "recon": {
        "description": (
            "Dispatch a read-only exploration query to one or more agents. "
            "Returns a recon_id immediately. Agents independently read the "
            "codebase to answer the query. Use get_recon_status to poll for "
            "completion, then get_recon_results for the raw answers. Use when "
            "you need to understand the codebase before writing a spec."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The exploration question to answer",
                },
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Paths to search within (e.g. [\"./\"])",
                },
                "agents": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Model strings; 'default' uses the configured model",
                    "default": ["default"],
                },
            },
            "required": ["query", "paths"],
        },
        "requires_token": False,
        "mcp": None,
        "method": None,
    },
    "get_recon_status": {
        "description": "Get the status of a recon query.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "recon_id": {
                    "type": "string",
                    "description": "Recon ID returned by recon",
                },
            },
            "required": ["recon_id"],
        },
        "requires_token": False,
        "mcp": None,
        "method": None,
    },
    "get_recon_results": {
        "description": (
            "Get the raw results of a completed recon query. Returns one "
            "result per agent. Results are raw text — synthesise them into "
            "a spec."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recon_id": {
                    "type": "string",
                    "description": "Recon ID returned by recon",
                },
            },
            "required": ["recon_id"],
        },
        "requires_token": False,
        "mcp": None,
        "method": None,
    },
}

# Map protocol tool names (from mode.tools) to concrete MCP tool names
MODE_TOOL_MAP = {
    "edit": ["read_file", "list_files", "list_models", "resolve_model", "recon", "get_recon_status", "get_recon_results"],
    "decide": ["propose_adjudicate", "propose_set_model"],
    "dispatch": ["dispatch_task", "get_job_status", "list_jobs", "get_job_logs"],
    "test": ["run_tests"],
    "validate": ["run_tests"],
    "review": ["read_file", "list_files", "read_diff", "get_status", "recon", "get_recon_status", "get_recon_results"],
    "approve": ["stage_files", "commit"],
    "commit": ["stage_files", "commit"],
    "merge": ["create_branch", "stage_files", "commit", "merge_branch", "delete_branch"],
    "pr": [
        "create_pr", "read_pr_diff", "post_review_comment",
        "approve_pr", "reject_pr", "merge_pr",
    ],
    "plan": ["decompose", "generate_spec", "validate_plan"],
}
