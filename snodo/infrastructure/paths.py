"""Shared path resolution for Snodo user directories (backward-compatibility shim).

FILE: snodo/infrastructure/paths.py
"""

from snodo.paths import resolve_home, resolve_project_root, require_project_root

__all__ = [
    "resolve_home",
    "resolve_project_root",
    "require_project_root",
]
