"""Protocol capability names and their MCP tool expansions."""

MODE_TOOL_MAP = {
    "edit": ["read_file", "list_files", "list_models", "resolve_model", "recon", "get_recon_status", "get_recon_results"],
    "write": ["write_file"],
    "decide": ["propose_adjudicate", "propose_set_model"],
    "dispatch": ["dispatch_task", "get_job_status", "list_jobs", "get_job_logs", "watch_job", "retry_job"],
    "test": ["run_tests"],
    "validate": ["run_tests"],
    "review": ["read_file", "list_files", "read_diff", "get_status", "recon", "get_recon_status", "get_recon_results"],
    "approve": ["stage_files", "commit"],
    "commit": ["stage_files", "commit"],
    "merge": ["create_branch", "stage_files", "commit", "merge_branch", "delete_branch"],
    "pr": [
        "create_change_request", "read_change_request_diff",
        "post_change_request_comment", "approve_change_request",
        "request_change_request_changes", "merge_change_request",
        "read_change_request_discussion", "create_pr", "read_pr_diff",
        "post_review_comment", "approve_pr", "reject_pr", "merge_pr",
    ],
    "plan": [
        "decompose", "generate_spec", "validate_plan",
        "propose_plan", "get_plan", "run_plan", "record_task_status",
        "queue_list", "queue_create", "queue_move", "queue_remove", "queue_validate", "queue_run",
    ],
    "queue": ["queue_list", "queue_create", "queue_move", "queue_remove", "queue_validate", "queue_run"],
    "read": ["read_file", "list_files"],
}
