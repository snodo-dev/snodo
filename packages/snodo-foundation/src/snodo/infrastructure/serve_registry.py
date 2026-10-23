"""User-level registry of running Snodo HTTP serve processes."""
from __future__ import annotations

import fcntl
import json
import logging
import os
import socket
import threading
from pathlib import Path

from snodo.infrastructure.atomic_json import atomic_write_json
from snodo.infrastructure.process_identity import process_identity_matches
from snodo.paths import resolve_home

_LOG = logging.getLogger(__name__)
_FILE = "serve_registry.json"
_THREAD_LOCK = threading.RLock()


def _path() -> Path:
    return resolve_home() / _FILE


def _locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return open(path.parent / ".serve_registry.lock", "a", encoding="utf-8")


def _read(path: Path) -> list[dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, list) and all(isinstance(item, dict) for item in value):
            return value
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return []


def _live(entry: dict) -> bool:
    pid = entry.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    identity = process_identity_matches(pid, entry.get("started_at"), entry.get("host"))
    return identity is not False


def list_serve_processes() -> list[dict]:
    """Return live entries, pruning dead or reused process IDs on disk."""
    path = _path()
    try:
        with _THREAD_LOCK, _locked(path) as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            entries = [entry for entry in _read(path) if _live(entry)]
            atomic_write_json(path, entries, trailing_newline=True)
            return entries
    except Exception:  # noqa: BLE001 — registry is advisory, never blocks serve
        _LOG.debug("Unable to read serve registry", exc_info=True)
        return []


def record_serve_process(project: str, mode: str, port: int | None, hostname: str | None = None) -> dict | None:
    """Record this process; registry failures are deliberately non-fatal."""
    path = _path()
    try:
        import psutil
        entry = {"project": str(Path(project).resolve()), "mode": mode or "", "port": port,
                 "hostname": hostname or "", "pid": os.getpid(),
                 "started_at": psutil.Process().create_time(), "host": socket.gethostname()}
        with _THREAD_LOCK, _locked(path) as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            entries = _read(path)
            entries = [item for item in entries if (item.get("project"), item.get("mode")) != (entry["project"], entry["mode"]) and _live(item)]
            entries.append(entry)
            atomic_write_json(path, entries, trailing_newline=True)
        return entry
    except Exception:  # noqa: BLE001 — advisory state must never block serve
        _LOG.debug("Unable to record serve process", exc_info=True)
        return None


def remove_serve_process(entry: dict | None) -> None:
    """Remove the exact run record, without disturbing a replacement entry."""
    if not entry:
        return
    path = _path()
    try:
        with _THREAD_LOCK, _locked(path) as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            entries = [item for item in _read(path) if item != entry]
            atomic_write_json(path, entries, trailing_newline=True)
    except Exception:  # noqa: BLE001 — cleanup must never block serve exit
        _LOG.debug("Unable to remove serve process record", exc_info=True)
