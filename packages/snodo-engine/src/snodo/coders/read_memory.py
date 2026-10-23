"""Read-memory tracking helpers for the LiteLLM coder adapter."""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_logger = logging.getLogger(__name__)

def _canonical_rel_path(value: Any, project_root: Optional[Path] = None) -> str:
    """Normalize a path argument the same way the workspace resolves it.

    Deduplication must not depend on spelling: './x', 'x/', 'a//b', 'a\\b' and
    an absolute path under the project root all address one file, and the
    resolver treats them as one, so the dedup key must too (Fixes #184).
    """
    p = str(value).replace("\\", "/").strip()
    if not p:
        return "."
    path = Path(p)
    if project_root is not None and path.is_absolute():
        try:
            path = path.resolve().relative_to(project_root)
        except (ValueError, OSError) as e:
            # Outside the root (or unresolvable): keep the absolute form so
            # the dedup key is at least stable across calls.
            _logger.debug("Dedup key kept absolute for path outside root %s: %s", project_root, e)
    return path.as_posix()


def _normalize_path_arg(args: Dict[str, Any], project_root: Optional[Path] = None) -> str:
    """Extract and normalize a path argument string."""
    for path_key in ("path", "directory", "file_path", "target_file", "file"):
        val = args.get(path_key)
        if val and isinstance(val, str):
            if val.strip():
                return _canonical_rel_path(val, project_root)
    return ""


def _extract_line_range(tool_name: str, args: Dict[str, Any]) -> Tuple[int, float]:
    """Extract (start_line, end_line) range from tool args."""
    if tool_name in ("read_file", "read_files"):
        return (1, float("inf"))

    if tool_name in ("read_file_lines", "read_lines"):
        start = args.get("start") or args.get("start_line") or 1
        end = args.get("end") or args.get("end_line") or float("inf")
        try:
            start_int = int(start)
        except (ValueError, TypeError):
            start_int = 1
        try:
            end_num = float(end)
        except (ValueError, TypeError):
            end_num = float("inf")
        return (start_int, end_num)

    return (1, float("inf"))


_READ_TOOLS = frozenset({
    "read_files",
    "read_file",
    "read_file_lines",
    "read_lines",
    "list_files",
    "list_directory",
    "ls",
    "git_show",
    "read_diff_between_refs",
    "git_log",
    "search_symbol",
    "search_string",
})


class ReadMemoryTracker:
    """Tracks read file contents, line ranges, directory listings, and tool arguments across turns."""

    def __init__(self, project_root: Optional[Any] = None) -> None:
        #: Same root the workspace resolves against; lets dedup keys treat
        #: every spelling of one path as one path (Fixes #184).
        self._project_root: Optional[Path] = None
        if project_root:
            try:
                self._project_root = Path(str(project_root)).resolve()
            except Exception:
                self._project_root = None
        # Exact canonical (tool_name, norm_args_json) -> turn_idx (1-based)
        self.exact_reads: Dict[Tuple[str, str], int] = {}
        # file_path -> [(start_line, end_line, turn_idx), ...]
        self.file_ranges: Dict[str, List[Tuple[int, float, int]]] = {}
        # canonical dir_path -> turn_idx
        self.dir_listings: Dict[str, int] = {}

    def check_read(self, tool_name: str, args: Dict[str, Any]) -> Optional[int]:
        """Check if a read tool call is already covered by memory.

        Returns the 1-based turn_idx if covered, else None.
        """
        if tool_name not in _READ_TOOLS:
            return None

        # 1. Exact argument match
        exact_key = _canonical_read_key(tool_name, args, self._project_root)
        if exact_key and exact_key in self.exact_reads:
            return self.exact_reads[exact_key]

        # 2. Batch read check
        if tool_name == "read_files":
            paths = args.get("paths", [])
            if isinstance(paths, list) and paths:
                all_read = True
                first_turn = None
                for p in paths:
                    norm_p = _canonical_rel_path(p, self._project_root) if isinstance(p, str) and p.strip() else ""
                    if not norm_p or norm_p not in self.file_ranges:
                        all_read = False
                        break
                    if first_turn is None:
                        first_turn = self.file_ranges[norm_p][0][2]
                if all_read:
                    return first_turn

        # 3. File line range / whole file coverage check
        if tool_name in ("read_file", "read_file_lines", "read_lines"):
            path = _normalize_path_arg(args, self._project_root)
            if path and path in self.file_ranges:
                req_start, req_end = _extract_line_range(tool_name, args)
                # Count only ranges wholly contained in a prior response as
                # repeats: a new or partially overlapping range brings fresh
                # source lines and must remain progress for exploration.
                for cov_start, cov_end, turn_idx in self.file_ranges[path]:
                    if cov_start <= req_start and cov_end >= req_end:
                        return turn_idx

        # 4. Directory listing check
        if tool_name in ("list_files", "list_directory", "ls"):
            dir_path = _normalize_path_arg(args, self._project_root) or "."
            if dir_path in self.dir_listings:
                return self.dir_listings[dir_path]

        return None

    def record_read(self, tool_name: str, args: Dict[str, Any], turn_idx: int) -> None:
        """Record a successful read tool execution in memory."""
        exact_key = _canonical_read_key(tool_name, args, self._project_root)
        if exact_key:
            self.exact_reads[exact_key] = turn_idx

        if tool_name == "read_files":
            paths = args.get("paths", [])
            if isinstance(paths, list):
                for p in paths:
                    if isinstance(p, str) and p.strip():
                        norm_p = _canonical_rel_path(p, self._project_root)
                        if norm_p not in self.file_ranges:
                            self.file_ranges[norm_p] = []
                        self.file_ranges[norm_p].append((1, float("inf"), turn_idx))

        if tool_name in ("read_file", "read_file_lines", "read_lines"):
            path = _normalize_path_arg(args, self._project_root)
            if path:
                req_start, req_end = _extract_line_range(tool_name, args)
                if path not in self.file_ranges:
                    self.file_ranges[path] = []
                self.file_ranges[path].append((req_start, req_end, turn_idx))

        if tool_name in ("list_files", "list_directory", "ls"):
            dir_path = _normalize_path_arg(args, self._project_root) or "."
            if dir_path not in self.dir_listings:
                self.dir_listings[dir_path] = turn_idx

    def export_paths(self) -> Dict[str, List[str]]:
        """Return the paths this attempt inspected, WITHOUT their contents.

        ``files`` are paths opened via read_file/read_file_lines/read_lines;
        ``directories`` are paths listed via list_files/list_directory/ls.

        Contents are deliberately excluded (Fixes #157 follow-up on recovery
        cold-start): between attempts the tree changes, and handing a later
        attempt a cached version of a file it must now fix would make a stale
        view authoritative — worse than re-reading. Only the map of *where the
        previous attempt looked* is safe to carry forward.
        """
        return {
            "files": sorted(self.file_ranges.keys()),
            "directories": sorted(self.dir_listings.keys()),
        }


def _canonical_read_key(
    name: str, args: Dict[str, Any], project_root: Optional[Path] = None,
) -> Optional[Tuple[str, str]]:
    """Return (tool_name, canonical_args_str) for read tools, or None if not a read tool.

    The key must describe the CALL, not the spelling: an optional parameter
    left absent is the same call as one sent at its default, a null parameter
    is the same as an absent one, and every path alias the resolver unifies
    must unify here too — otherwise the dedup that is supposed to bound the
    search/read loop lets one call become ninety (Fixes #184).
    """
    if name not in _READ_TOOLS:
        return None

    norm_args: Dict[str, Any] = {}
    for key, val in args.items():
        if val is None:
            continue  # a null optional parameter is an omitted one
        if key == "paths" and isinstance(val, list):
            norm_args[key] = [
                _canonical_rel_path(p, project_root)
                for p in val
                if isinstance(p, str) and p.strip()
            ]
        elif key in ("path", "directory", "file_path", "target_file", "file") and isinstance(val, str):
            norm_args[key] = _canonical_rel_path(val, project_root)
        else:
            norm_args[key] = val

    # 'directory' defaults to "." — explicit and implicit are one call.
    if norm_args.get("directory") == ".":
        norm_args.pop("directory")

    try:
        args_str = json.dumps(norm_args, sort_keys=True)
    except Exception:
        args_str = str(norm_args)

    return (name, args_str)


def format_repeat_read_response(tool_name: str, args: Dict[str, Any], prev_turn: int) -> str:
    """Format a concise tool response pointing to the previous turn containing the result."""
    if tool_name in ("search_string", "search_symbol"):
        term_key = "query" if tool_name == "search_string" else "name"
        term = str((args or {}).get(term_key) or "").strip()
        if term:
            return (
                f"{tool_name} for '{term}' was already run in Turn {prev_turn}. "
                f"Refer to the tool response from Turn {prev_turn} for its results. "
                "Repeating this call returns the same output and wastes a turn — "
                "search for a different term, read a specific file, or proceed to submit_files."
            )
    target = _normalize_path_arg(args)
    if tool_name in ("read_file", "read_files", "read_file_lines", "read_lines"):
        req_start, req_end = _extract_line_range(tool_name, args)
        range_str = f"lines {req_start}-{int(req_end)}" if req_end != float("inf") else "full file"
        if target:
            return (
                f"File '{target}' ({range_str}) was already fetched using {tool_name} in Turn {prev_turn}. "
                f"Refer to the tool response from Turn {prev_turn} for its content."
            )
    if tool_name in ("list_files", "list_directory", "ls"):
        dir_name = target or "."
        return (
            f"Directory '{dir_name}' was already listed using {tool_name} in Turn {prev_turn}. "
            f"Refer to the tool response from Turn {prev_turn} for its contents."
        )
    if target:
        return (
            f"'{target}' was already fetched using {tool_name} in Turn {prev_turn}. "
            f"Refer to the tool response from Turn {prev_turn} for its content."
        )
    return (
        f"Tool '{tool_name}' with identical arguments was already executed in Turn {prev_turn}. "
        f"Refer to the tool response from Turn {prev_turn} for its content."
    )
