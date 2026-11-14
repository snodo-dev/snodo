"""Persistence for the web dashboard's project scan roots."""

import os
import tempfile
from pathlib import Path
from typing import Iterable

import yaml

from snodo.paths import resolve_home


def _web_file() -> Path:
    return resolve_home() / "web.yml"


def load_roots() -> list[str]:
    """Load saved project roots, returning an empty list for absent/bad data."""
    try:
        with _web_file().open(encoding="utf-8") as stream:
            data = yaml.safe_load(stream)
        roots = data.get("roots") if isinstance(data, dict) else None
        if not isinstance(roots, list):
            return []
        return [str(Path(root).expanduser().resolve()) for root in roots
                if isinstance(root, str) and root.strip()]
    except (OSError, UnicodeError, yaml.YAMLError, TypeError, ValueError):
        return []


def save_roots(roots: Iterable[str | os.PathLike[str]]) -> None:
    """Atomically save normalized roots to the user's ``web.yml`` file."""
    normalized: list[str] = []
    seen: set[str] = set()
    for root in roots:
        path = str(Path(root).expanduser().resolve())
        if path not in seen:
            seen.add(path)
            normalized.append(path)

    target = _web_file()
    content = yaml.safe_dump({"roots": normalized}, sort_keys=False, allow_unicode=True)
    try:
        if target.read_text(encoding="utf-8") == content:
            return
    except FileNotFoundError:
        pass

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=target.parent,
            prefix=f".{target.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temp_path = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, target)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
