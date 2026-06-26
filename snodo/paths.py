"""Shared path resolution for Snodo user directories.

FILE: snodo/paths.py

Resolves the ~/.snodo-equivalent directory from the
SNODO_HOME environment variable when set, falling back to the
platform home directory.
"""

import os
from pathlib import Path
from typing import Optional


def resolve_home() -> Path:
    """Return the Snodo home directory.

    Reads SNODO_HOME from the environment.  When set it replaces
    ~/.snodo entirely — config, sessions, memory all live under
    the given path.

    Returns:
        Path to the Snodo home directory.
    """
    if "SNODO_HOME" in os.environ:
        return Path(os.environ["SNODO_HOME"])
    return Path.home() / ".snodo"


def resolve_project_root(start: Optional[str] = None) -> Optional[str]:
    """Walk up from *start* (or cwd) looking for a .snodo/ directory.

    Returns the directory that contains .snodo (the project root),
    or None if no .snodo is found anywhere up to the filesystem root.

    ``~/.snodo/`` (global config directory) is explicitly excluded
    from project-marker detection.
    """
    home = Path.home()
    directory = Path(start).resolve() if start else Path.cwd()
    for parent in [directory] + list(directory.parents):
        if parent == home:
            continue  # ~/.snodo is global config, not a project marker
        if (parent / ".snodo").is_dir():
            return str(parent)
    return None


def require_project_root(start: Optional[str] = None) -> str:
    """Resolve the project root or raise a clear error.

    Calls resolve_project_root; raises SystemExit with a message
    when no .snodo directory is found in this or any parent.
    """
    root = resolve_project_root(start)
    if root is None:
        raise SystemExit(
            "Error: Not inside a Snodo project "
            "(no .snodo found in this or any parent directory)"
        )
    return root
