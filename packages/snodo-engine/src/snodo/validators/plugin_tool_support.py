"""Helpers for exposing configured validator-tool plugins to the LLM loop."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from typing import Any, Mapping

from snodo.validators.tool_registry import ValidatorToolContext, _default_registry


def configured_tools(validator_spec: Any) -> dict[str, Mapping[str, Any]]:
    """Return configured plugin tool names and their options."""
    configured: dict[str, Mapping[str, Any]] = {}
    for entry in getattr(validator_spec, "validator_tools", None) or []:
        if isinstance(entry, str):
            configured[entry] = {}
        elif isinstance(entry, Mapping):
            configured[str(entry.get("name", ""))] = entry.get("options", {})
        else:
            configured[entry.name] = entry.options
    return {name: options for name, options in configured.items() if name}


def workspace_root(workspace: Any) -> Path:
    """Return the available workspace root for read-only plugin context."""
    root = getattr(workspace, "project_root", None)
    return Path(root) if root else Path.cwd()


def is_tool_argument_error(error: Exception) -> bool:
    """Classify invalid read-tool inputs separately from operational failures."""
    from snodo.tools.workspace import PathValidationError

    if isinstance(error, (PathValidationError, FileNotFoundError, KeyError, TypeError)):
        return True
    message = str(error).lower()
    return isinstance(error, ValueError) and any(marker in message for marker in (
        "path is not a ", "directory not found:", "file not found:",
        "start must be", "end must be", "invalid path",
    ))


def tool_access_instruction(validator_spec: Any) -> str:
    """Return the configured restriction on unavailable validator tools."""
    if not getattr(validator_spec, "check_tool_access", False):
        return ""
    declared = ", ".join(getattr(validator_spec, "tools", []) or []) or "(none)"
    return (
        "This validator has check_tool_access enabled. If any criterion asks "
        "for verification requiring a capability not in this validator's "
        f"declared tools ({declared}), do not pass it. Submit a blocker and "
        "include a tool_access_missing object with the exact criterion and "
        "missing capability.\n"
    )


def execute_builtin_tool(name: str, args: dict[str, Any], workspace: Any, git: Any) -> str:
    """Execute one of the LLM validator's built-in read-only tools."""
    handlers = {
        "read_diff_between_refs": lambda: git.diff_between_refs(args["ref1"], args["ref2"]),
        "git_show": lambda: git.show(args["ref"], args["path"]),
        "git_log": lambda: git.log(args.get("n", 5)),
        "read_file": lambda: workspace.read_file(args["path"]),
        "read_file_lines": lambda: workspace.read_file_lines(args["path"], args["start"], args["end"]),
        "list_files": lambda: "\n".join(workspace.list_files(args.get("directory", "."))),
        "summarize_directory": lambda: workspace.summarize_directory(args.get("directory", ".")),
    }
    handler = handlers.get(name)
    return handler() if handler else f"Unknown tool: {name}"


def offer_definitions(configured: Mapping[str, Mapping[str, Any]]) -> tuple[list[dict], dict[str, Any], list[str]]:
    """Resolve configured plugin definitions and identify unavailable names."""
    definitions, tools, missing = [], {}, []
    for name in configured:
        tool = _default_registry.lookup(name)
        if tool is None:
            missing.append(name)
            continue
        definitions.append(dict(tool.definition))
        tools[name] = tool
    return definitions, tools, missing


def execute_plugin_tool(
    tool: Any,
    arguments: Mapping[str, Any],
    *,
    workspace_root: Path,
    spec_text: str,
    validator_id: str,
    options: Mapping[str, Any],
    diff_refs: tuple[str, str] | None,
    timeout: float = 30.0,
) -> str:
    """Run a plugin handler with read-only inputs and bound its execution time."""
    context = ValidatorToolContext(
        workspace_root=workspace_root,
        spec_text=spec_text,
        validator_id=validator_id,
        tool_options=options,
        diff_refs=diff_refs,
    )
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(tool.handler, arguments, context)
    try:
        return str(future.result(timeout=timeout))
    except TimeoutError:
        future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)
        return f"Tool error: TimeoutError: handler timed out after {timeout:g}s"
    except Exception as exc:
        return f"Tool error: {type(exc).__name__}: {exc}"
    finally:
        if future.done():
            executor.shutdown(wait=True)
