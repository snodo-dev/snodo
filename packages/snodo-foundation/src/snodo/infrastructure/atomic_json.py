"""Atomic JSON file writes shared by Snodo state stores."""

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_json(
    path: str | Path,
    data: Any,
    *,
    indent: int = 2,
    trailing_newline: bool = False,
) -> None:
    """Write JSON through a unique same-directory temporary file."""
    target = Path(path)
    fd, temporary_path = tempfile.mkstemp(
        dir=str(target.parent), prefix=f".{target.name}-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=indent)
            if trailing_newline:
                handle.write("\n")
        os.replace(temporary_path, target)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise
