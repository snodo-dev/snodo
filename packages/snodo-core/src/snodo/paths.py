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
    from snodo.project import _is_system_root_or_temp

    if not start and "SNODO_PROJECT_ROOT" in os.environ:
        candidate = Path(os.environ["SNODO_PROJECT_ROOT"]).resolve()
        if not _is_system_root_or_temp(candidate) and (candidate / ".snodo").is_dir():
            return str(candidate)

    snodo_home = resolve_home()
    directory = Path(start).resolve() if start else Path.cwd()
    for parent in [directory] + list(directory.parents):
        if (
            parent == snodo_home
            or (parent / ".snodo") == snodo_home
            or _is_system_root_or_temp(parent)
        ):
            continue  # ~/.snodo and /tmp are not project markers
        if (parent / ".snodo").is_dir():
            return str(parent)

    if "SNODO_PROJECT_ROOT" in os.environ:
        candidate = Path(os.environ["SNODO_PROJECT_ROOT"]).resolve()
        if not _is_system_root_or_temp(candidate) and (candidate / ".snodo").is_dir():
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


def get_project_local_home_rel(project_root: Optional[Path | str] = None) -> Optional[str]:
    """Return relative POSIX path of snodo home if it lies inside project_root, or None.

    When SNODO_HOME is configured to a directory inside the repository, snodo
    treats that directory as sensitive state (config.yml with credentials, tokens.db,
    checkpoints.db) and ensures it is ignored and excluded from coder operations.
    """
    try:
        home = resolve_home().resolve()
        if project_root is None:
            resolved_root = resolve_project_root()
            if resolved_root is None:
                return None
            root = Path(resolved_root).resolve()
        else:
            root = Path(project_root).resolve()

        rel = home.relative_to(root)
        if rel == Path("."):
            return None
        return rel.as_posix()
    except (ValueError, TypeError, RuntimeError):
        return None


def is_protected_workspace_path(path: str | Path, workspace: Path | str) -> bool:
    """Return True if path is within .snodo/ or a repository-local snodo home."""
    if not path:
        return False
    try:
        p = Path(path)
        parts = p.parts
        if not parts:
            return False
        if parts[0] == ".snodo":
            return True

        local_home_rel = get_project_local_home_rel(workspace)
        if local_home_rel:
            home_parts = Path(local_home_rel).parts
            if len(parts) >= len(home_parts) and parts[:len(home_parts)] == home_parts:
                return True
    except (ValueError, TypeError, RuntimeError):
        return False
    return False

