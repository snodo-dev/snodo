"""Best-effort content-addressed uploads for live task specifications."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from urllib.parse import quote

import httpx

from snodo.infrastructure.paths import resolve_home

_logger = logging.getLogger(__name__)
MAX_SPEC_CHARACTERS = 20_000
_lock = threading.Lock()
_in_flight: set[str] = set()
_retry_at: dict[str, float] = {}
_CACHE_LIMIT = 5000


def spec_hash(spec: str) -> str | None:
    """Hash exact UTF-8 content, excluding specs beyond the cloud limit."""
    if len(spec) > MAX_SPEC_CHARACTERS:
        return None
    return hashlib.sha256(spec.encode("utf-8")).hexdigest()


def schedule_upload(spec: str, base_url: str, api_key: str) -> str | None:
    """Return its hash and upload off-thread unless it is already accepted."""
    digest = spec_hash(spec)
    if digest is None or is_uploaded(digest):
        return digest
    with _lock:
        if time.monotonic() < _retry_at.get(digest, 0.0):
            return digest
        if digest in _in_flight:
            return digest
        _in_flight.add(digest)
    thread = threading.Thread(
        target=_upload, args=(digest, spec, base_url, api_key), daemon=True,
    )
    thread.start()
    return digest


def prepare_snapshot(snapshot: dict, root: str, base_url: str, api_key: str) -> dict:
    """Add hashes to task entries and queue bounded spec uploads."""
    from copy import deepcopy

    result = deepcopy(snapshot)
    for task in _task_entries(result):
        spec = _read_spec(Path(root), task["id"])
        if spec is None or len(spec) > MAX_SPEC_CHARACTERS:
            continue
        digest = schedule_upload(spec, base_url, api_key)
        if digest is not None:
            task["spec_hash"] = digest
    return result


def delivery_suppressed(root: str, session_id: str) -> bool:
    """Honor the existing mock/benchmark delivery exclusion for spec bytes."""
    if os.environ.get("SNODO_BENCHMARK") == "1":
        return True
    try:
        lines = (Path(root) / ".snodo" / "audit.log").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            data = json.loads(line).get("data", {})
        except (ValueError, AttributeError):
            continue
        if data.get("session_id") != session_id:
            continue
        usage = data.get("usage", [])
        if data.get("coder") == "mock" or any(
            isinstance(item, dict) and item.get("coder") == "mock"
            for item in usage if isinstance(usage, list)
        ):
            return True
    return False


def _task_entries(snapshot: dict):
    yield from snapshot.get("tasks", [])
    for plan in snapshot.get("plans", []):
        yield from plan.get("tasks", [])
        for wave in plan.get("waves", []):
            yield from wave.get("tasks", [])


def _read_spec(root: Path, task_id: str) -> str | None:
    """Read known task-spec locations without ever putting text in liveness."""
    snodo = root / ".snodo"
    candidates = [
        snodo / "tasks" / task_id / name
        for name in ("task.md", "spec.md", "description.md", "task.json", "state.json")
    ]
    for path in snodo.glob(f"plans/**/{task_id}_task.md"):
        candidates.append(path)
    for path in candidates:
        try:
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                if path.suffix == ".json":
                    value = json.loads(text)
                    text = value.get("description") or value.get("spec") or ""
                return text if isinstance(text, str) else None
        except (OSError, ValueError, AttributeError):
            continue
    return None


def _cache_path() -> Path:
    return resolve_home() / "cloud_live_specs.json"


def is_uploaded(digest: str) -> bool:
    try:
        data = json.loads(_cache_path().read_text(encoding="utf-8"))
        return digest in data.get("hashes", [])
    except (OSError, ValueError, AttributeError):
        return False


def _mark_uploaded(digest: str) -> None:
    path = _cache_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        hashes = data.get("hashes", [])
    except (OSError, ValueError, AttributeError):
        hashes = []
    path.parent.mkdir(parents=True, exist_ok=True)
    hashes = [item for item in hashes if item != digest] + [digest]
    path.write_text(json.dumps({"hashes": hashes[-_CACHE_LIMIT:]}), encoding="utf-8")


def _upload(digest: str, spec: str, base_url: str, api_key: str) -> None:
    url = f"{base_url.rstrip('/')}/v1/live/specs/{quote(digest, safe='')}"
    try:
        response = httpx.put(
            url, content=spec.encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "text/plain; charset=utf-8"},
            timeout=10.0,
        )
        if 200 <= response.status_code < 300 or response.status_code == 409:
            _mark_uploaded(digest)
        elif response.status_code == 429:
            delay = 1.0
            try:
                delay = max(0.0, float(response.json().get("retry_after", delay)))
            except (ValueError, TypeError, AttributeError):
                pass
            with _lock:
                _retry_at[digest] = time.monotonic() + delay
        else:
            _logger.warning("Live spec upload failed: %s -> HTTP %s", url, response.status_code)
    except Exception as exc:  # best effort; next snapshot retries
        _logger.warning("Live spec upload failed: %s -> %s", url, exc)
    finally:
        with _lock:
            _in_flight.discard(digest)
