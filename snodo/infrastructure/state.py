"""Per-project runtime state — .snodo/state.json.

FILE: snodo/infrastructure/state.py (Task 7.19)

The HI-CTRL architecture stores current_mode and active_session
per project so that `snodo run` knows which mode to execute in
without requiring the user to specify it on every invocation.

Atomic writes (temp file + rename) match the session.py pattern.
"""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class ProjectState:
    """Per-project runtime state stored in .snodo/state.json."""

    current_mode: str = ""
    active_session: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


def read_state(project_root: str) -> ProjectState:
    """Read project state from .snodo/state.json.

    Returns a default ProjectState if the file does not exist.
    """
    path = Path(project_root) / ".snodo" / "state.json"
    if not path.exists():
        return ProjectState()
    try:
        data = json.loads(path.read_text())
        return ProjectState(**data)
    except (json.JSONDecodeError, OSError, TypeError):
        return ProjectState()


def write_state(project_root: str, state: ProjectState) -> None:
    """Atomically write project state to .snodo/state.json."""
    snodo_dir = Path(project_root) / ".snodo"
    snodo_dir.mkdir(parents=True, exist_ok=True)
    state_path = snodo_dir / "state.json"
    tmp_path = snodo_dir / "state.json.tmp"

    payload = {
        "current_mode": state.current_mode,
        "active_session": state.active_session,
        "metadata": state.metadata,
    }
    tmp_path.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(str(tmp_path), str(state_path))
