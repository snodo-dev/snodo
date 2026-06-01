"""Shared path resolution for Snodo user directories.

FILE: snodo/infrastructure/paths.py (Task 7.12)

Resolves the ~/.snodo-equivalent directory from the
SNODO_HOME environment variable when set, falling back to the
platform home directory.
"""

import os
from pathlib import Path


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
