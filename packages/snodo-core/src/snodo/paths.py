"""Shared path resolution for Snodo user directories.

FILE: snodo/paths.py

Resolves the ~/.snodo-equivalent directory from the
SNODO_HOME environment variable when set, falling back to the
platform home directory.
"""

import hashlib
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
        return Path(os.environ["SNODO_HOME"]).expanduser()
    return Path.home() / ".snodo"


def resolve_token_store() -> Path:
    """Return the path to the shared consumed-token store (SQLite).

    Defaults to ``<snodo home>/tokens.db``.  Overridable via
    ``SNODO_TOKEN_STORE`` so read-only-FS deployments can point the store
    at a writable location (there is deliberately no "unsafe/skip" mode).
    """
    if "SNODO_TOKEN_STORE" in os.environ:
        return Path(os.environ["SNODO_TOKEN_STORE"]).expanduser()
    return resolve_home() / "tokens.db"


def resolve_project_root(start: Optional[str] = None) -> Optional[str]:
    """Walk up from *start* (or cwd) looking for a .snodo/ directory.

    Returns the directory that contains .snodo (the project root),
    or None if no .snodo is found anywhere up to the filesystem root.

    ``~/.snodo/`` (global config directory) is explicitly excluded
    from project-marker detection.
    """
    if not start and "SNODO_PROJECT_ROOT" in os.environ:
        candidate = Path(os.environ["SNODO_PROJECT_ROOT"]).resolve()
        if (candidate / ".snodo").is_dir():
            return str(candidate)

    home = Path.home()
    directory = Path(start).resolve() if start else Path.cwd()
    for parent in [directory] + list(directory.parents):
        if parent == home:
            continue  # ~/.snodo is global config, not a project marker
        if (parent / ".snodo").is_dir():
            return str(parent)

    if "SNODO_PROJECT_ROOT" in os.environ:
        candidate = Path(os.environ["SNODO_PROJECT_ROOT"]).resolve()
        if (candidate / ".snodo").is_dir():
            return str(candidate)

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


def derive_task_id(description: str) -> str:
    """Derive a stable, collision-resistant task id from a task description.

    Uses SHA-256 (not the built-in ``hash()``, which is salted per process via
    ``PYTHONHASHSEED``) so the same description yields the same id across
    interpreter invocations.  The truncated digest is 48 bits, which removes the
    practical collision risk of the previous 24-bit ``hash() & 0xffffff`` scheme.

    The id is load-bearing: it keys the session checkpoint, names the git
    branch/worktree, and is bound into the validation token.  Determinism is
    intentional — re-running the same spec produces the same id, which is what
    retry/resume flows expect.
    """
    return f"task_{hashlib.sha256(description.encode()).hexdigest()[:12]}"
