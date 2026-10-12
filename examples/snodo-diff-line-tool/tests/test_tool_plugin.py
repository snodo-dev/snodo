"""Exercise the example through the installed entry-point registry."""

from pathlib import Path

from snodo.validators.tool_registry import ValidatorToolContext, ValidatorToolRegistry


def test_entry_point_discovers_tool_and_handler_returns_diff_count() -> None:
    registry = ValidatorToolRegistry()
    registry.discover_plugins()

    tool = registry.lookup("count_diff_lines")
    assert tool is not None
    assert tool.name == "count_diff_lines"

    diffs: list[tuple[str, str]] = []

    def read_diff(ref1: str, ref2: str) -> str:
        diffs.append((ref1, ref2))
        return (
            "diff --git a/src/app.py b/src/app.py\n"
            "--- a/src/app.py\n+++ b/src/app.py\n"
            "@@ -1 +1,2 @@\n-old\n+new\n+extra\n"
            "diff --git a/docs/readme.md b/docs/readme.md\n"
            "--- a/docs/readme.md\n+++ b/docs/readme.md\n"
            "@@ -1 +1 @@\n-before\n+after\n"
        )

    context = ValidatorToolContext(
        workspace_root=Path("."),
        spec_text="Count implementation changes",
        validator_id="diff_review",
        diff_refs=("base", "head"),
        diff_accessor=read_diff,
    )
    result = tool.handler({"path_glob": "src/*.py"}, context)

    assert result == "Diff contains 3 changed lines in files matching 'src/*.py'."
    assert diffs == [("base", "head")]
