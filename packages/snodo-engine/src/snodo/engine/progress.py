"""Progress formatting and turn-observability helpers.

FILE: snodo/engine/progress.py (Issue #51)

Surfaces real-time LLM tool-loop progress (elapsed time and tool turns) for
coders and validators without visual noise or control characters.
"""

import json
from typing import Any, List, Optional


def format_elapsed(seconds: float) -> str:
    """Format elapsed seconds into m:ss format (e.g. 0:04, 1:12)."""
    secs = max(0, int(seconds))
    m, s = divmod(secs, 60)
    return f"{m}:{s:02d}"


def format_tool_call_summary(tool_calls: Optional[List[Any]]) -> str:
    """Format a list of tool call objects into a clean, human-readable turn summary."""
    if not tool_calls:
        return "(no tools called)"

    formatted = []
    for tc in tool_calls:
        func = getattr(tc, "function", None)
        name = getattr(func, "name", None) or getattr(tc, "name", "unknown_tool")
        raw_args = getattr(func, "arguments", None) or getattr(tc, "arguments", {})

        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args)
            except Exception:
                args = {}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}

        if name == "read_file":
            path = args.get("path", "")
            formatted.append(f"read_file({path})")
        elif name == "read_file_lines":
            path = args.get("path", "")
            start = args.get("start", "")
            end = args.get("end", "")
            formatted.append(f"read_file_lines({path}:{start}-{end})")
        elif name == "list_files":
            directory = args.get("directory", ".")
            formatted.append(f"list_files({directory})")
        elif name == "submit_files":
            files = args.get("files", [])
            count = len(files) if isinstance(files, list) else ""
            formatted.append(f"submit_files({count} file(s))" if count != "" else "submit_files")
        elif name == "submit_verdict":
            sev = args.get("severity", "")
            formatted.append(f"submit_verdict({sev})" if sev else "submit_verdict")
        else:
            # General fallback for any other tool
            first_arg = next((str(v) for k, v in args.items() if isinstance(v, (str, int))), "")
            formatted.append(f"{name}({first_arg})" if first_arg else f"{name}")

    return ", ".join(formatted)
