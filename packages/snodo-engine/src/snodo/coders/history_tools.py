"""Read-only repository history tools for dispatched coders."""

from pathlib import Path
from typing import Any

from snodo.coders.tool_result_text import bound_tool_result
from snodo.tools.git import GitError, GitMCP
from snodo.tools.workspace import PathValidationError


def definitions() -> list[dict[str, Any]]:
    """Return the coder-facing, read-only git tool definitions."""
    return [
        {"type": "function", "function": {"name": "git_log", "description": "Read commits, optionally limited to a repository path", "parameters": {"type": "object", "properties": {"n": {"type": "integer", "default": 5}, "path": {"type": "string", "description": "Optional repository-relative path"}}}}},
        {"type": "function", "function": {"name": "git_show", "description": "Read a file at a git revision", "parameters": {"type": "object", "properties": {"ref": {"type": "string"}, "path": {"type": "string"}}, "required": ["ref", "path"]}}},
        {"type": "function", "function": {"name": "read_diff_between_refs", "description": "Read the difference between two git revisions", "parameters": {"type": "object", "properties": {"ref1": {"type": "string"}, "ref2": {"type": "string"}}, "required": ["ref1", "ref2"]}}},
    ]


def execute(name: str, args: dict[str, Any], workspace: Any) -> str:
    """Run one history query, confined to the workspace repository."""
    root = Path(workspace.project_root).resolve()
    git = GitMCP(str(root))
    try:
        if name in {"git_show", "git_log"} and args.get("path"):
            path = str(args["path"])
            resolved = git.validate_path(path)
            if ".snodo" in resolved.relative_to(root).parts:
                raise PathValidationError("Path is protected under .snodo/ and cannot be read from repository history")
        if name == "git_log":
            n = max(1, min(int(args.get("n", 5)), 100))
            path = args.get("path")
            result = git.repo.git.log("--oneline", f"-{n}", "--", str(path)) if path else git.log(n)
        elif name == "git_show":
            result = git.show(str(args["ref"]), str(args["path"]))
        elif name == "read_diff_between_refs":
            result = git.diff_between_refs(str(args["ref1"]), str(args["ref2"]))
        else:
            return f"Unknown tool: {name}"
        return bound_tool_result(result)
    except (GitError, PathValidationError, KeyError, ValueError) as exc:
        ref = args.get("ref") or args.get("ref1") or args.get("ref2")
        if ref:
            return f"Could not read repository history for revision {ref!r}: {exc}. Check the ref with git_log and try a reachable revision."
        return f"Could not read repository history: {exc}"


def is_gemini3_plus(model: str) -> bool:
    """Check whether a model identifier denotes Gemini 3 or later."""
    return "gemini-3" in model.lower() or "gemini-4" in model.lower()
