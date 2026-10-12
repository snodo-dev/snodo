"""Read-only validator tool that counts diff lines matching a path glob."""

from fnmatch import fnmatch
from typing import Any, Mapping

from snodo.validators.tool_registry import ValidatorTool, ValidatorToolContext


def _count_matching_lines(diff_text: str, path_glob: str) -> int:
    current_matches = False
    count = 0
    for line in diff_text.splitlines():
        if line.startswith("+++ b/"):
            current_matches = fnmatch(line.removeprefix("+++ b/"), path_glob)
        elif current_matches and line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            count += 1
    return count


def handle_count(arguments: Mapping[str, Any], context: ValidatorToolContext) -> str:
    """Count added and removed lines for paths matching the requested glob."""
    path_glob = arguments["path_glob"]
    if context.diff_accessor is None or context.diff_refs is None:
        return "Diff references are unavailable for this validation."
    diff_text = context.diff_accessor(*context.diff_refs)
    count = _count_matching_lines(diff_text, path_glob)
    return f"Diff contains {count} changed lines in files matching {path_glob!r}."


tool = ValidatorTool(
    definition={
        "type": "function",
        "function": {
            "name": "count_diff_lines",
            "description": "Count added and removed diff lines in files matching a path glob.",
            "parameters": {
                "type": "object",
                "properties": {"path_glob": {"type": "string"}},
                "required": ["path_glob"],
                "additionalProperties": False,
            },
        },
    },
    handler=handle_count,
)
