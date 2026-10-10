"""MCP tool schemas and mode-to-tool mappings.

Extracted from mcp/server.py to isolate static tool definitions.

A tool is governed by the protocol, not by a token the caller must hold:
which tools a server exposes is the mode's capability grant (MODE_TOOL_MAP
below), and the validator quorum is enforced inside the engine loop, per
task. Nothing here demands that the caller carry a validation token.
"""

from snodo.protocols.capabilities import MODE_TOOL_MAP

# Tool schemas: name -> {description, inputSchema, mcp, method}
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
        "mcp": "workspace",
        "method": "read_file",
    },
    "write_file": {
        "description": "Write content to a file under protocol-allowed project path prefixes",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path relative to project root"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
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
        "mcp": "shell",
        "method": "run_tests",
    },
    "read_diff": {
        "description": "Read current git diff",
        "inputSchema": {"type": "object", "properties": {}},
        "mcp": "git",
        "method": "read_diff",
    },
    "get_status": {
        "description": "Get git status",
        "inputSchema": {"type": "object", "properties": {}},
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
        "mcp": "git",
        "method": "delete_branch",
    },
    "create_change_request": {
        "description": "Create a change request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "branch": {"type": "string", "description": "Source branch name"},
                "title": {"type": "string", "description": "Change request title"},
                "body": {"type": "string", "description": "Change request description body"},
            },
            "required": ["branch", "title", "body"],
        },
        "mcp": "pr",
        "method": "create_change_request",
    },
    "read_change_request_diff": {
        "description": "Read the diff of a change request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "change_request_id": {"type": "string", "description": "Opaque change request identifier"},
            },
        "required": ["change_request_id"],
        },
        "mcp": "pr",
        "method": "read_change_request_diff",
    },
    "post_change_request_comment": {
        "description": "Post a comment on a change request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "change_request_id": {"type": "string", "description": "Opaque change request identifier"},
                "comment": {"type": "string", "description": "Comment text"},
            },
        "required": ["change_request_id", "comment"],
        },
        "mcp": "pr",
        "method": "post_change_request_comment",
    },
    "approve_change_request": {
        "description": "Approve a change request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "change_request_id": {"type": "string", "description": "Opaque change request identifier"},
            },
        "required": ["change_request_id"],
        },
        "mcp": "pr",
        "method": "approve_change_request",
    },
    "request_change_request_changes": {
        "description": "Request changes on a change request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "change_request_id": {"type": "string", "description": "Opaque change request identifier"},
                "reason": {"type": "string", "description": "Reason for requesting changes"},
            },
        "required": ["change_request_id", "reason"],
        },
        "mcp": "pr",
        "method": "request_change_request_changes",
    },
    "merge_change_request": {
        "description": "Merge a change request",
        "inputSchema": {
            "type": "object",
            "properties": {
                "change_request_id": {"type": "string", "description": "Opaque change request identifier"},
            },
        "required": ["change_request_id"],
        },
        "mcp": "pr",
        "method": "merge_change_request",
    },
    "read_change_request_discussion": {
        "description": "Read discussion and reviews on a change request",
        "inputSchema": {"type": "object", "properties": {
            "change_request_id": {"type": "string", "description": "Opaque change request identifier"},
        }, "required": ["change_request_id"]},
        "mcp": "pr",
        "method": "read_change_request_discussion",
    },
    "create_pr": {
        "description": "Deprecated: use create_change_request",
        "inputSchema": {"type": "object", "properties": {
            "branch": {"type": "string", "description": "Source branch name"},
            "title": {"type": "string", "description": "PR title"},
            "body": {"type": "string", "description": "PR description body"},
        }, "required": ["branch", "title", "body"]}, "mcp": "pr", "method": "create_pr",
    },
    "read_pr_diff": {
        "description": "Deprecated: use read_change_request_diff",
        "inputSchema": {"type": "object", "properties": {"pr_number": {"type": "integer", "description": "PR number"}}, "required": ["pr_number"]}, "mcp": "pr", "method": "read_pr_diff",
    },
    "post_review_comment": {
        "description": "Deprecated: use post_change_request_comment",
        "inputSchema": {"type": "object", "properties": {"pr_number": {"type": "integer", "description": "PR number"}, "comment": {"type": "string", "description": "Comment text"}}, "required": ["pr_number", "comment"]}, "mcp": "pr", "method": "post_review_comment",
    },
    "approve_pr": {
        "description": "Deprecated: use approve_change_request",
        "inputSchema": {"type": "object", "properties": {"pr_number": {"type": "integer", "description": "PR number"}}, "required": ["pr_number"]}, "mcp": "pr", "method": "approve_pr",
    },
    "reject_pr": {
        "description": "Deprecated: use request_change_request_changes",
        "inputSchema": {"type": "object", "properties": {"pr_number": {"type": "integer", "description": "PR number"}, "reason": {"type": "string", "description": "Reason for rejection"}}, "required": ["pr_number", "reason"]}, "mcp": "pr", "method": "reject_pr",
    },
    "merge_pr": {
        "description": "Deprecated: use merge_change_request",
        "inputSchema": {"type": "object", "properties": {"pr_number": {"type": "integer", "description": "PR number"}}, "required": ["pr_number"]}, "mcp": "pr", "method": "merge_pr",
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
        "mcp": "planner",
        "method": "decompose",
    },
    "generate_spec": {
        "description": "Generate a task specification file within a plan. Name module when the task is confined to one declared module; it selects that module's test gate and bounds writable paths.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_name": {"type": "string", "description": "Plan name"},
                "task_id": {
                    "type": "string",
                    "description": (
                        "Required format <wave>.<sequence>_<name>; the name is "
                        "required (e.g., 1.1_models, not 1.1)"
                    ),
                },
                "spec": {"type": "string", "description": "Task specification content"},
                "module": {"type": "string", "description": "Optional declared module scope. Use for a task confined to one module; selects its test command and bounds writable paths."},
                "parent_task_ref": {"type": "string", "description": "ID of parent task if this is a sub-task"},
                "replace": {"type": "boolean", "description": "Allow overwriting existing task spec"},
            },
            "required": ["plan_name", "task_id", "spec"],
        },
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
        "mcp": "planner",
        "method": "validate_plan",
    },
    "propose_plan": {
        "description": (
            "Turn an intent into a proposed plan on disk (.snodo/plans/<name>/). "
            "Returns the plan structure (name, intent, waves with ids, "
            "dependencies and tasks) and its validation state — nothing "
            "executes. Add tasks with generate_spec, check the shape with "
            "validate_plan while authoring, then run_plan."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "description": "The intent/goal the plan is for"},
                "plan_name": {"type": "string", "description": "Plan name (stable identifier, used as directory name)"},
                "waves": {"type": "integer", "description": "Number of sequential wave slots to scaffold (default 1)"},
            },
            "required": ["intent", "plan_name"],
        },
        "mcp": None,
        "method": None,
    },
    "get_plan": {
        "description": (
            "Retrieve a plan by name: name, intent, waves (ids, depends_on, "
            "tasks), per-task status, validation state, and a compact "
            "latest-run summary (outcome, halt type, attempts, duration, cost, "
            "delivery, and totals). Use `snodo plan status <name> --run-summary` "
            "for the detailed CLI report. Read from the "
            "plan files on disk, which are the source of truth; callable any "
            "time, including while a run is in progress."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_name": {"type": "string", "description": "Plan name"},
            },
            "required": ["plan_name"],
        },
        "mcp": None,
        "method": None,
    },
    "record_task_status": {
        "description": (
            "Record a task's status as an operator's account made outside the "
            "loop — the orchestrator equivalent of `snodo task complete`. Use "
            "it when a person finished (or otherwise resolved) a task by hand "
            "and that decision must advance the plan: it writes the same "
            "status.json the loop writes and appends the same audit event the "
            "CLI's `snodo task complete` appends, with the same vocabulary and "
            "provenance. It records what a human decided and decides nothing: "
            "it is not a validator verdict, cannot stand in for one, and the "
            "audit entry says it was not engine-judged. A recorded `completed` "
            "is never permission — only the engine's own quorum can pass a "
            "task — and the tool mutates no other part of the plan."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_name": {"type": "string", "description": "Plan the task belongs to"},
                "task_id": {"type": "string", "description": "Task identifier"},
                "status": {
                    "type": "string",
                    "enum": ["pending", "in_progress", "completed", "blocked", "errored", "unmerged"],
                    "description": "The status the operator recorded — the plan's own vocabulary",
                },
                "who": {"type": "string", "description": "Person (or role) the status is recorded for"},
                "notes": {"type": "string", "description": "Why the operator decided this, alongside who did"},
            },
            "required": ["plan_name", "task_id", "status", "who"],
        },
        "mcp": None,
        "method": None,
    },
    "run_plan": {
        "description": (
            "Start a plan run as a background job and return its job_id "
            "immediately — a plan run is a job like any other. A wave takes "
            "minutes, so do NOT expect this call to carry the run: follow the "
            "returned job_id with get_job_status (poll until completed / "
            "failed / unmerged), list_jobs, and get_job_logs. Per-task detail "
            "stays available from get_plan at any time. The plan's structure "
            "is checked before anything spawns (calling validate_plan first "
            "is a convenience while authoring, not a precondition), and every "
            "task the run dispatches passes the engine's own validator quorum "
            "inside the loop, at its own dispatch boundary — the guarantee "
            "lives per task, where the work actually happens. Refuses without "
            "executing anything if the plan does not conform. Pass wait=true "
            "(with optional timeout, default 3600s) only when you genuinely "
            "want to block until the run finishes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_name": {"type": "string", "description": "Plan name to execute"},
                "wave": {"type": "integer", "description": "Optional: run only this wave id"},
                "model": {"type": "string", "description": "Optional coder model override"},
                "mock": {"type": "boolean", "description": "Use the mock coder instead of a real LLM"},
                "no_isolation": {"type": "boolean", "description": "Run tasks in the working tree instead of isolated worktrees"},
                "protocol": {"type": "string", "description": "Protocol file path (default: .snodo/protocol.yml)"},
                "wait": {"type": "boolean", "description": "Opt in to blocking until the run finishes (default false); returns final status or names the still-running job on timeout"},
                "timeout": {"type": "number", "description": "Seconds to wait when wait=true (default 3600)"},
            },
            "required": ["plan_name"],
        },
        "mcp": None,
        "method": None,
    },
    "queue_list": {
        "description": "List named queues and their plans in order, with status derived from each plan's records (same as `snodo queue`)",
        "inputSchema": {"type": "object", "properties": {}},
        "mcp": None, "method": None,
    },
    "queue_create": {
        "description": "Create an empty named plan queue; refuses invalid or already-existing queue names (same as `snodo queue create`)",
        "inputSchema": {"type": "object", "properties": {"name": {"type": "string", "description": "Name for the new queue"}}, "required": ["name"]},
        "mcp": None, "method": None,
    },
    "queue_move": {
        "description": "Move a queued plan within or between queues; refuses running plans and invalid positions (same as `snodo queue move`)",
        "inputSchema": {"type": "object", "properties": {
            "plan": {"type": "string", "description": "Queued plan to move"},
            "front": {"type": "boolean", "description": "Move to the front"},
            "before": {"type": "string", "description": "Place before another plan"},
            "after": {"type": "string", "description": "Place after another plan"},
            "queue": {"type": "string", "description": "Destination queue"},
        }, "required": ["plan"]},
        "mcp": None, "method": None,
    },
    "queue_remove": {
        "description": "Remove a plan from whichever queue holds it without changing its plan records; refuses running plans (same as `snodo queue remove`)",
        "inputSchema": {"type": "object", "properties": {
            "plan": {"type": "string", "description": "Queued plan to remove"},
        }, "required": ["plan"]},
        "mcp": None, "method": None,
    },
    "queue_validate": {
        "description": "Report queue readiness, plan verification, dependencies, and active runners without changing queue state (same as `snodo queue validate`)",
        "inputSchema": {"type": "object", "properties": {"queue": {"type": "string", "description": "Queue name; omit to report all queues"}}},
        "mcp": None, "method": None,
    },
    "queue_run": {
        "description": "Run one or more plan queues as an asynchronous background job; follows CLI queue ordering, blocking, and refusal rules. Poll the returned job_id with get_job_status and get_job_logs.",
        "inputSchema": {"type": "object", "properties": {
            "queues": {"type": "string", "description": "Queue name or comma-separated queue names (defaults to default)"},
            "all": {"type": "boolean", "description": "Run every queue sequentially in creation order"},
            "non_blocking": {"type": "boolean", "description": "Continue past unfinished plans"},
            "parallel_run": {"type": "integer", "minimum": 1, "description": "Maximum concurrent plans per queue"},
            "protocol": {"type": "string", "default": ".snodo/protocol.yml", "description": "Protocol file path"},
            "mock": {"type": "boolean", "description": "Use mock coder"},
        }},
        "mcp": None, "method": None,
    },
    "dispatch_task": {
        "description": "Dispatch a task for execution via the protocol engine",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_spec": {"type": "string", "description": "Task specification to dispatch"},
                "coding_model": {"type": "string", "description": "Optional model for the coder (overrides config default)"},
                "module": {"type": "string", "description": "Optional declared module scope for a task confined to one module; selects its test command and bounds writable paths."},
            },
            "required": ["task_spec"],
        },
        "mcp": None,
        "method": None,
    },
    "get_job_status": {
        "description": (
            "Poll execution status of one dispatched job and its full task "
            "spec. Call after dispatch_task, run_plan, or queue_run returns a job id. "
            "Status progresses: queued → running → completed | failed. Check "
            "for completed + exit_code=0 to confirm success. For a halted task, "
            "review its diff and validator verdicts, then follow the recommended "
            "next action in the job/task status text."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID returned by dispatch_task, run_plan, or queue_run"},
            },
            "required": ["job_id"],
        },
        "mcp": None,
        "method": None,
    },
    "list_jobs": {
        "description": (
            "List all jobs for this project as bounded one-line summaries: "
            "id, status, exit_code, task_ref, title, timestamps, duration. "
            "Cheap to call repeatedly — task specs are not included; read "
            "one job's spec with get_job_status."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
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
                "job_id": {"type": "string", "description": "Job ID returned by dispatch_task or run_plan"},
                "stream": {"type": "string", "description": "stdout or stderr", "default": "stdout"},
                "tail": {"type": "integer", "description": "Return only the last N lines", "default": 50},
            },
            "required": ["job_id"],
        },
        "mcp": None,
        "method": None,
    },
    "watch_job": {
        "description": (
            "Open a live view of a background job in an MCP Apps host, with a "
            "plain-text snapshot for clients without UI support. Use after "
            "dispatch_task, run_plan, or queue_run returns a job_id."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID returned by dispatch_task, run_plan, or queue_run"},
            },
            "required": ["job_id"],
        },
        "mcp": None,
        "method": None,
    },
    "validate_task": {
        "description": (
            "Run the pre-execute validators and report the quorum's outcome. "
            "Returns one of four validation outcomes (pass/escalate/blocker/"
            "validator_error); an execution halt can additionally be "
            "environment_error, a non-verdict operational fault — see ADR 015. "
            "A pass records a single-use token that the next dispatch_task "
            "consumes as the audit link between the quorum and the work; no "
            "tool call is refused for want of a token the caller holds."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "Task identifier"},
                "task_spec": {"type": "string", "description": "Optional task specification the validators evaluate"},
                "plan_name": {"type": "string", "description": "Optional owning plan name, when validating a task from a plan"},
            },
            "required": ["task_id"],
        },
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
        "mcp": None,
        "method": None,
    },
    "recon": {
        "description": (
            "Dispatch a read-only exploration query to one or more agents. "
            "Returns a recon_id immediately. Agents independently read the "
            "codebase to answer the query. Use get_recon_status to poll for "
            "completion, then get_recon_results for the raw answers. Use when "
            "code can answer a question before you ask the operator, to recover "
            "lost context, or before writing a spec."
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
                    "description": (
                        "Explicit model names to run as agents. Omit to use "
                        "the configured llm.recon.models; 'default' names the "
                        "configured default model."
                    ),
                },
                "num_agents": {
                    "type": "integer",
                    "description": "Number of agents to fan out (uses config llm.recon.num_agents if omitted). Ignored if explicit agents list provided.",
                },
            },
            "required": ["query", "paths"],
        },
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
        "mcp": None,
        "method": None,
    },
    "retry_job": {
        "description": (
            "Retry the task associated with a failed job. Looks up the "
            "task_id from the job's state and dispatches a new run. By "
            "default the task keeps the specification it is recorded with — "
            "an operational failure needs another attempt, not a new spec. "
            "By default it continues from the previous attempt's worktree and "
            "committed or uncommitted changes. Set fresh_start=true to start "
            "from main instead. "
            "Pass append_spec to add guidance on top of that spec; pass "
            "revised_spec only to replace it (the replaced spec is audited "
            "as spec_replaced and stays recoverable)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "job_id": {
                    "type": "string",
                    "description": "Job ID (e.g., j_abc123) to retry",
                },
                "append_spec": {
                    "type": "string",
                    "description": (
                        "Guidance added on top of the recorded spec; the "
                        "spec itself is kept"
                    ),
                },
                "revised_spec": {
                    "type": "string",
                    "description": "Replacement specification (discards the recorded one)",
                },
                "fresh_start": {
                    "type": "boolean",
                    "description": "Start from main instead of continuing the previous attempt's worktree (default false)",
                },
            },
            "required": ["job_id"],
        },
        "mcp": None,
        "method": None,
    },
    "survey": {
        "description": (
            "Read-only repository survey returning the same JSON as `snodo survey --json`. "
            "The default deterministic pass does not call a model; set agent=true "
            "to allow configured agent boundary judgements, which may incur model calls."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent": {
                    "type": "boolean",
                    "default": False,
                    "description": "Allow configured agent boundary judgements and model calls",
                },
            },
        },
        "mcp": None,
        "method": None,
    },
    "intake": {
        "description": "Read-only validator-criteria proposals, returning the same JSON as `snodo intake --json`; it never writes or prompts",
        "inputSchema": {"type": "object", "properties": {}},
        "mcp": None,
        "method": None,
    },
    "ready": {
        "description": "Read-only project readiness assessment, returning the same JSON as `snodo ready --json`; it does not append an audit event",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "description": "Optional mode used to filter displayed findings"},
                "protocol": {"type": "string", "default": ".snodo/protocol.yml", "description": "Protocol file path"},
            },
        },
        "mcp": None,
        "method": None,
    },
    "protocol_schema": {
        "description": "Return the generated protocol JSON Schema publication. Read-only; does not inspect or modify protocol.yml.",
        "inputSchema": {"type": "object", "properties": {}},
        "mcp": None,
        "method": None,
    },
}

def unknown_capability_warnings(protocol) -> list[str]:
    """Describe mode grants that have no MCP capability mapping.

    Kept in snodo-mcp because the protocol verifier in snodo-foundation must
    not depend on the higher-level MCP package.
    """
    known = ", ".join(sorted(MODE_TOOL_MAP))
    return [
        f"Mode '{mode.mode_id}' grants unknown capability '{grant}'. "
        f"Known capabilities: {known}."
        for mode in protocol.modes
        for grant in mode.tools
        if grant not in MODE_TOOL_MAP
    ]

# These diagnostics are the read-only project-understanding surface. Like the
# guide, they are available regardless of the active mode's write capability.
PROJECT_DIAGNOSTIC_TOOLS = ["survey", "intake", "ready", "protocol_schema"]

# The planning surface — the human gate above the task loop. A server pinned
# to a single mode exposes these only when its mode grants the "plan"
# capability (MODE_TOOL_MAP above). An unpinned server resolves the current
# mode and does not add planning tools outside that capability grant.
PLANNING_TOOLS = [
    "decompose",
    "generate_spec",
    "validate_plan",
    "propose_plan",
    "get_plan",
    "run_plan",
    "record_task_status",
    "queue_list",
    "queue_create",
    "queue_move",
    "queue_remove",
    "queue_validate",
    "queue_run",
]

# The read-only job-observation surface: the tools that answer "how is the
# job I started doing?". They mutate nothing, so exposing them costs no
# authority.
JOB_OBSERVATION_TOOLS = [
    "get_job_status",
    "list_jobs",
    "get_job_logs",
    "watch_job",
]

# The read-only recon-observation surface, by the same rule as the job one.
RECON_OBSERVATION_TOOLS = [
    "get_recon_status",
    "get_recon_results",
]

# Tools that start background work, each mapped to the read-only tools that
# observe the work it starts. A job_id with no way to ask after it is not a
# contract an orchestrator can honour, so the pairing is not left to the
# capability grant: ProtocolMCPServer._resolve_tools enforces it on every
# server it builds, and the two surfaces cannot come apart. Only read-only
# observers travel — a starter never drags a mutating tool along.
WORK_STARTING_TOOLS = {
    "dispatch_task": JOB_OBSERVATION_TOOLS,
    "run_plan": JOB_OBSERVATION_TOOLS,
    "queue_run": JOB_OBSERVATION_TOOLS,
    "recon": RECON_OBSERVATION_TOOLS,
}
