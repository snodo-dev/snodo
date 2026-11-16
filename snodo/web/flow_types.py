"""Fast, read-only lookup of task flow types from Snodo audit logs."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

_FLOW_TYPES = frozenset({"feature", "defect", "debt", "risk"})
_CACHE_VERSION = 1


def _cache_path(project: Path) -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    identity = hashlib.sha256(str(project.resolve()).encode("utf-8")).hexdigest()
    return base / "snodo" / "flow-types" / f"{identity}.json"


def _time_key(value: Any) -> tuple[int, Any]:
    """Normalize audit timestamps for ordering; missing timestamps sort earliest."""
    if isinstance(value, (int, float)):
        return (2, float(value))
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return (2, parsed.timestamp())
        except (OverflowError, OSError, ValueError):
            return (1, value)
    return (0, "")


def _scan(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        with path.open("rb") as stream:
            for line in stream:
                if b"task_classified" not in line:
                    continue
                try:
                    record = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(record, dict) or record.get("event_type") != "task_classified":
                    continue
                data = record.get("data")
                if not isinstance(data, dict):
                    continue
                task = data.get("task_ref")
                flow_type = data.get("flow_type")
                if isinstance(task, str) and task and flow_type in _FLOW_TYPES:
                    events.append({"task_ref": task, "flow_type": flow_type,
                                   "timestamp": record.get("timestamp")})
    except OSError:
        pass
    return events


def _load_disk_cache(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and value.get("version") == _CACHE_VERSION:
            files = value.get("files")
            if isinstance(files, dict):
                return files
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        pass
    return {}


def _write_disk_cache(path: Path, records: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".flow-types-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"version": _CACHE_VERSION, "files": records}, stream)
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    except OSError:
        pass


def task_flow_types(project_path: str | Path) -> dict[str, str]:
    """Return task_ref → latest known flow type for a project.

    Rotated audit files are reused while their path, size, and mtime_ns stay
    unchanged. Cache failures are deliberately non-fatal; no project files are
    ever written.
    """
    audit_dir = Path(project_path) / ".snodo"
    try:
        rotated = sorted(p for p in audit_dir.glob("audit.log-*") if p.is_file())
        # Also support the dot-delimited naming convention documented by Snodo.
        rotated.extend(sorted(p for p in audit_dir.glob("audit.log.*") if p.is_file()))
    except OSError:
        rotated = []
    try:
        current = audit_dir / "audit.log"
        has_current = current.is_file()
    except OSError:
        has_current = False

    cache_file = _cache_path(Path(project_path))
    cached = _load_disk_cache(cache_file)
    fresh_cache: dict[str, Any] = {}
    indexed: list[list[dict[str, Any]]] = []
    for path in rotated:
        try:
            stat = path.stat()
            signature = [stat.st_size, stat.st_mtime_ns]
        except OSError:
            continue
        key = str(path.resolve())
        entry = cached.get(key)
        if (isinstance(entry, dict) and entry.get("signature") == signature
                and isinstance(entry.get("events"), list)):
            events = entry["events"]
        else:
            events = _scan(path)
        fresh_cache[key] = {"signature": signature, "events": events}
        indexed.append(events)
    if fresh_cache != cached:
        _write_disk_cache(cache_file, fresh_cache)

    if has_current:
        indexed.append(_scan(current))

    latest: dict[str, tuple[tuple[int, Any], int, str]] = {}
    order = 0
    for events in indexed:
        for event in events:
            order += 1
            key = (_time_key(event.get("timestamp")), order, event["flow_type"])
            task = event["task_ref"]
            if task not in latest or key[:2] >= latest[task][:2]:
                latest[task] = key
    return {task: item[2] for task, item in latest.items()}
