"""Per-project runtime state — .snodo/state.json.

FILE: snodo/infrastructure/state.py (Task 7.19)

The HI-CTRL architecture stores current_mode and active_session
per project so that `snodo run` knows which mode to execute in
without requiring the user to specify it on every invocation.

Atomic writes (temp file + rename) match the session.py pattern.
"""

import fcntl
import json
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict

_logger = logging.getLogger(__name__)
_STATE_MUTATION_LOCK = threading.Lock()


def _json_default(obj: Any) -> Any:
    """Custom JSON serializer for objects like datetime, timedelta, Path."""
    import datetime

    if isinstance(obj, datetime.timedelta):
        return round(obj.total_seconds() * 1000, 2)
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)


def atomic_update_json(
    target_dir: Path | str,
    filename: str,
    updater_fn: Callable[[Dict[str, Any]], None],
) -> None:
    """Atomically and safely update a JSON file in target_dir.

    Thread-safe and process-safe:
    - Uses thread lock for within-process concurrency (e.g. litellm callbacks).
    - Uses file flock on target_dir/.<filename>.lock for cross-process concurrency.
    - Writes to a unique temporary file and atomically renames with os.replace.
    """
    dir_path = Path(target_dir)
    dir_path.mkdir(parents=True, exist_ok=True)
    target_file = dir_path / filename
    lock_file_path = dir_path / f".{filename}.lock"

    with _STATE_MUTATION_LOCK:
        try:
            with open(lock_file_path, "a") as lock_f:
                try:
                    fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
                except (OSError, IOError):  # noqa: S110
                    pass

                data: Dict[str, Any] = {}
                if target_file.exists():
                    try:
                        with open(target_file, "r", encoding="utf-8") as f:
                            content = f.read().strip()
                            if content:
                                data = json.loads(content)
                    except Exception as e:
                        _logger.debug("Failed to read %s for update: %s", target_file, e)
                        data = {}

                if not isinstance(data, dict):
                    data = {}

                # Apply mutation
                updater_fn(data)

                # Write to unique temp file
                tmp_fd, tmp_path_str = tempfile.mkstemp(
                    dir=str(dir_path), prefix=f"{filename}_", suffix=".tmp"
                )
                try:
                    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                        json.dump(data, f, indent=2, default=_json_default)
                        f.write("\n")
                    os.replace(tmp_path_str, str(target_file))
                except Exception:
                    try:
                        os.unlink(tmp_path_str)
                    except Exception:  # noqa: S110
                        pass
                    raise
                finally:
                    try:
                        fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
                    except (OSError, IOError):  # noqa: S110
                        pass
        except Exception as e:
            _logger.debug("Failed to atomically update %s: %s", target_file, e)


@dataclass
class ProjectState:
    """Per-project runtime state stored in .snodo/state.json."""

    current_mode: str = ""
    active_session: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


def read_state(project_root: str) -> ProjectState:
    """Read project state from .snodo/state.json.

    Returns a default ProjectState if the file does not exist.
    Old-format ``active_session: null`` or single-string values are
    migrated cleanly to the per-mode dict.
    """
    path = Path(project_root) / ".snodo" / "state.json"
    if not path.exists():
        return ProjectState()
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return ProjectState()

    # Migrate old single-string active_session to per-mode dict
    raw = data.get("active_session")
    if isinstance(raw, str):
        data["active_session"] = {}  # old string → empty dict (no per-mode info)
    elif not isinstance(raw, dict):
        data["active_session"] = {}

    try:
        return ProjectState(**data)
    except TypeError:
        return ProjectState()


def write_state(project_root: str, state: ProjectState) -> None:
    """Atomically write project state to .snodo/state.json."""
    snodo_dir = Path(project_root) / ".snodo"
    payload = {
        "current_mode": state.current_mode,
        "active_session": state.active_session,
        "metadata": state.metadata,
    }

    def _update(data: dict) -> None:
        data.update(payload)

    atomic_update_json(snodo_dir, "state.json", _update)
