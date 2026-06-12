"""Snapshot tests for MCP tool registry and mode-to-tool mappings.

Catches accidental tool additions, removals, or mode/tool assignment changes.
"""


from snodo.mcp.tools import TOOL_REGISTRY, MODE_TOOL_MAP


EXPECTED_TOOL_KEYS = frozenset({
    "read_file",
    "write_file",
    "list_files",
    "delete_file",
    "run_tests",
    "read_diff",
    "get_status",
    "stage_files",
    "commit",
    "create_branch",
    "merge_branch",
    "delete_branch",
    "create_pr",
    "read_pr_diff",
    "post_review_comment",
    "approve_pr",
    "reject_pr",
    "merge_pr",
    "decompose",
    "generate_spec",
    "validate_plan",
    "dispatch_task",
    "get_job_status",
    "list_jobs",
    "get_job_logs",
    "list_models",
    "resolve_model",
    "propose_adjudicate",
    "propose_set_model",
    "validate_task",
    "recon",
    "get_recon_status",
    "get_recon_results",
    "retry_job",
})

EXPECTED_MODE_TOOL_MAP_KEYS = frozenset({
    "edit",
    "decide",
    "dispatch",
    "test",
    "validate",
    "review",
    "approve",
    "commit",
    "merge",
    "pr",
    "plan",
})


class TestToolRegistrySnapshot:
    """TOOL_REGISTRY keys must match the golden set."""

    def test_registry_keys_match_golden(self):
        actual_keys = frozenset(TOOL_REGISTRY.keys())
        assert actual_keys == EXPECTED_TOOL_KEYS, (
            f"TOOL_REGISTRY keys changed.\n"
            f"  Added:   {actual_keys - EXPECTED_TOOL_KEYS}\n"
            f"  Removed: {EXPECTED_TOOL_KEYS - actual_keys}"
        )

    def test_all_registry_entries_have_required_fields(self):
        required = {"description", "inputSchema", "requires_token", "mcp", "method"}
        for name, entry in TOOL_REGISTRY.items():
            missing = required - set(entry.keys())
            assert not missing, f"Tool '{name}' missing fields: {missing}"


class TestModeToolMapSnapshot:
    """MODE_TOOL_MAP structure must match the golden set."""

    def test_mode_keys_match_golden(self):
        actual_keys = frozenset(MODE_TOOL_MAP.keys())
        assert actual_keys == EXPECTED_MODE_TOOL_MAP_KEYS, (
            f"MODE_TOOL_MAP keys changed.\n"
            f"  Added:   {actual_keys - EXPECTED_MODE_TOOL_MAP_KEYS}\n"
            f"  Removed: {EXPECTED_MODE_TOOL_MAP_KEYS - actual_keys}"
        )

    def test_all_mapped_tools_exist_in_registry(self):
        for mode, tools in MODE_TOOL_MAP.items():
            for tool in tools:
                assert tool in TOOL_REGISTRY, (
                    f"Mode '{mode}' maps to tool '{tool}' which is not in TOOL_REGISTRY"
                )
