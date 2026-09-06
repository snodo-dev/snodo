"""Shared path resolution for Snodo user directories (backward-compatibility shim).

FILE: snodo/infrastructure/paths.py
"""

from snodo.paths import (  # noqa: F401
    resolve_home,
    resolve_project_root,
    require_project_root,
    get_project_local_home_rel,
    is_protected_workspace_path,
)

__all__ = [
    "resolve_home",
    "resolve_project_root",
    "require_project_root",
    "get_project_local_home_rel",
    "is_protected_workspace_path",
]
