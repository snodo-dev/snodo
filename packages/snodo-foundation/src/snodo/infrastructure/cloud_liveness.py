"""Cloud liveness — event-driven snapshot of what a session is running.

FILE: snodo/infrastructure/cloud_liveness.py

The ingest path (``cloud_sync.py``) carries HISTORY: append-only events with a
cursor that advances only on a 2xx, so a failed batch re-sends. That guarantee
is right for history and wrong for LIVENESS — "task 2.1 is in_progress" is
true for forty minutes and then false, and replaying a stale status after a
failed push is worse than the gap it covered. So liveness is a separate path
with opposite mechanics (Fixes #291):

- **Event-driven, not a heartbeat.** A push happens when something actually
  changes — a plan/task/job status transition, or an engine transition event
  observed through the audit log — never on a timer. Silence means nothing
  changed, not that the machine died.
- **At most once per 60 seconds per session**, so a burst of transitions
  coalesces into one write. The snapshot is built at send time inside the
  worker, so the coalesced write carries the *later* state. A transition that
  ends a run (a halt, a completion) bypasses the throttle: it is the only
  record that will tell the far side the run stopped, and a throttle that ate
  it would strand a false "running" until the next event.
- **A full snapshot, never a delta.** A lost push is harmless; the next one
  supersedes it entirely.
- **Keyed by session**, which is already project-scoped, already persisted,
  and already survives a restart. The project rides along so the far side can
  join on it.
- **A failed push is dropped**: no queue, no retry, no cursor, and no touch of
  ``CloudSyncState``. The next transition pushes current truth again.
- **Status and identity only** — never payloads, prompts, diffs, file
  contents, paths with home directories, or any user identifier. The
  credential in the Authorization header identifies the person; attribution
  is taken from it on the far side.

Opt-in exactly as sync is: with ``cloud.sync_enabled`` off or no
``cloud.api_key``, a run makes no network call on this path either.
"""

import atexit
import json
import logging
import threading
import time
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

from snodo.infrastructure.cloud_sync import _should_sync
from snodo.infrastructure.paths import resolve_home
from snodo.project import scope_for_project_id

_logger = logging.getLogger(__name__)

#: Per-session push budget: a burst of transitions coalesces into one write.
LIVENESS_THROTTLE_SECONDS = 60.0

#: Bounded tail read for "what the last event was and when".
_TAIL_BYTES = 64 * 1024

#: Engine transitions worth a liveness push. Anything not in this list is
#: either restatement (``session_decision_updated`` ships ~75% of all events)
#: or chain noise for the live view. The types are existing audit vocabulary;
#: only *when* they travel is changing (Fixes #291).
TRIGGER_EVENTS = frozenset({
    "dispatch",
    "transition",
    "halt",
    "task_complete",
    "task_merged",
    "token_consumed",
    "post_validation_route",
    "verification_executed",
    "unverified_merge_blocked",
    "execution_failed",
    "session_started",
    "session_task_changed",
})

#: Transitions that end a run's claim to be live. These bypass the throttle:
#: dropping the sole record of "this stopped" would strand a false "running"
#: until some later event happened to displace it.
TERMINAL_EVENTS = frozenset({
    "halt",
    "task_complete",
    "task_merged",
    "execution_failed",
    "unverified_merge_blocked",
})

#: Plan-task statuses that settle a task (the planner's own vocabulary). The
#: planner forces a push for these, and only these, so a burst of ordinary
#: writes still coalesces.
TERMINAL_PLAN_STATUSES = frozenset({
    "completed", "blocked", "errored", "unmerged",
})

#: Statuses that mean a plan task has not begun. Used only to decide whether
#: the session has *anything* worth publishing — never to rename a status on
#: the wire.
_PENDING_TASK_STATUS = "pending"

# Per-session push bookkeeping: session_id -> {"last_push": float|None,
# "in_flight": bool}. Guards the throttle check and the in-flight flag, not
# the network write.
_lock = threading.Lock()
_sessions: dict = {}

#: Consecutive failed or rejected liveness pushes across transitions.
#: A single rejection stays quiet (debug level), but a repeating rejection
#: is surfaced at warning level so the operator sees the wire is disconnected
#: without raising the log level (Fixes #293).
_consecutive_rejections: int = 0

#: Cache for the sync gate: it answers the same config question on every audit
#: transition, and a config load per event would put file IO on the run's
#: critical path. Re-read after _GATE_TTL_SECONDS or any explicit recheck.
_GATE_TTL_SECONDS = 5.0
_gate_cache: dict = {"value": None, "checked_at": 0.0}


def _sync_gate_open(config: Optional[dict] = None) -> bool:
    """``_should_sync`` behind a short-lived cache (Fixes #291)."""
    if config is not None:
        return _should_sync(config)
    now = time.monotonic()
    with _lock:
        fresh = (
            _gate_cache["value"] is not None
            and now - _gate_cache["checked_at"] < _GATE_TTL_SECONDS
        )
        if fresh:
            return bool(_gate_cache["value"])
    value = _should_sync(config)
    with _lock:
        _gate_cache["value"] = value
        _gate_cache["checked_at"] = now
    return value


def reset_liveness_state() -> None:
    """Forget all per-session throttle state and rejection counters. Test seam; production never calls it."""
    global _consecutive_rejections
    with _lock:
        _sessions.clear()
        _threads.clear()
        _gate_cache["value"] = None
        _gate_cache["checked_at"] = 0.0
        _consecutive_rejections = 0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def request_liveness_push(
    session_id: str,
    project_root: str,
    force: bool = False,
    config: Optional[dict] = None,
) -> bool:
    """Coalesce-check, then fire the snapshot push off-thread.

    Returns True when a push was started. A push in flight absorbs later
    transitions (the worker builds its snapshot at send time, so the wire
    write carries the newest state); a request inside the throttle window is
    dropped, because the next transition will push current truth anyway and a
    stale snapshot is worth less than nothing. ``force`` — reserved for
    terminal transitions — bypasses the window, never the in-flight merge.
    """
    if not session_id or not _sync_gate_open(config):
        return False
    now = time.monotonic()
    with _lock:
        st = _sessions.setdefault(session_id, {"last_push": None, "in_flight": False})
        if st["in_flight"]:
            return False
        if not force and st["last_push"] is not None \
                and now - st["last_push"] < LIVENESS_THROTTLE_SECONDS:
            return False
        st["in_flight"] = True
    from threading import Thread
    thread = Thread(target=_deliver, args=(session_id, project_root), daemon=True)
    with _lock:
        _threads.append(thread)
    thread.start()
    return True


#: Live push threads, drained once at process exit by :func:`wait_for_pushes`
#: with the same bounded-wait discipline the ingest flush uses: a slow cloud
#: must never hang the process, and an abandoned push is simply dropped.
_threads: list = []
_FLUSH_BUDGET = 3.0


def wait_for_pushes(timeout: float = _FLUSH_BUDGET) -> None:
    """Join in-flight liveness pushes, bounded, once, at exit (Fixes #291).

    The last push of a run — the one carrying its terminal state — starts in
    a daemon thread inside ``_execute_task``'s teardown; without this the
    process would exit before the snapshot reached the wire. Each thread gets
    at most the remaining budget; a thread still running when the budget is
    spent is abandoned (the push is dropped — that is the contract).
    """
    deadline = time.monotonic() + timeout
    while True:
        with _lock:
            if not _threads:
                return
            thread = _threads.pop(0)
        remaining = deadline - time.monotonic()
        if remaining > 0 and thread.is_alive():
            thread.join(timeout=remaining)


# Registered at import so every process that can start a push drains it at
# exit, bounded. A no-op when no push is pending. Daemon threads are only
# killed after all atexit handlers have run, so the ordering relative to the
# ingest flush (cloud_sync) costs wall-clock, not delivery.
atexit.register(wait_for_pushes)


def _deliver(session_id: str, project_root: str) -> None:
    """Build the snapshot at send time and push it once. Never raises, never retries."""
    try:
        snapshot = build_liveness_snapshot(session_id, project_root)
        if snapshot is None:
            # Nothing has run in this session: no status is true yet, and a
            # payload of "everything pending" would be noise (Fixes #291).
            return
        with _lock:
            _sessions.setdefault(
                session_id, {"last_push": None, "in_flight": False},
            )["last_push"] = time.monotonic()
        _post_snapshot(snapshot)
    except Exception as exc:  # noqa: BLE001 — a liveness push never disturbs the run
        _logger.debug("Liveness push failed for %s (dropped, not queued): %s", session_id, exc)
    finally:
        with _lock:
            st = _sessions.get(session_id)
            if st is not None:
                st["in_flight"] = False
            me = threading.current_thread()
            if me in _threads:
                _threads.remove(me)


def _post_snapshot(snapshot: dict, config: Optional[dict] = None) -> None:
    """PUT the snapshot to ``{liveness_url}/live/{session_id}``. Drop on any failure.

    Deliberately unlike the ingest path: no retry loop, no cursor, no
    CloudSyncState touch. A failed liveness push is superseded by the next
    transition's snapshot, which carries the *current* truth (Fixes #291).
    A single rejection is logged at debug level, but a repeating rejection is
    surfaced at warning level so disconnection is visible without raising the
    log level (Fixes #293).
    """
    global _consecutive_rejections
    import httpx

    from snodo.config import ConfigManager, get_cloud_liveness_url

    if config is None:
        config = ConfigManager().load()
    cloud = config.get("cloud", {}) if isinstance(config, dict) else {}
    api_key = (cloud.get("api_key") or "").strip()
    if not api_key:
        return
    liveness_url = get_cloud_liveness_url(config)
    url = f"{liveness_url.rstrip('/')}/live/{quote(snapshot['session_id'], safe='')}"
    body = json.dumps(snapshot).encode()
    try:
        response = httpx.put(
            url,
            content=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            timeout=10.0,
        )
        if 200 <= response.status_code < 300:
            with _lock:
                _consecutive_rejections = 0
        else:
            with _lock:
                _consecutive_rejections += 1
                streak = _consecutive_rejections
            if streak > 1:
                _logger.warning(
                    "Liveness push %s -> HTTP %d (repeated rejection, dropped): %s",
                    url, response.status_code, response.text[:200],
                )
            else:
                _logger.debug(
                    "Liveness push %s -> HTTP %d (dropped): %s",
                    url, response.status_code, response.text[:200],
                )
    except Exception as exc:  # noqa: BLE001 — dropped; the next transition re-pushes
        with _lock:
            _consecutive_rejections += 1
            streak = _consecutive_rejections
        if streak > 1:
            _logger.warning(
                "Liveness push %s failed (repeated rejection, dropped): %s",
                url, exc,
            )
        else:
            _logger.debug("Liveness push %s failed (dropped): %s", url, exc)


# ---------------------------------------------------------------------------
# Snapshot construction — status and identity, nothing else
# ---------------------------------------------------------------------------


def build_liveness_snapshot(session_id: str, project_root: str) -> Optional[dict]:
    """Assemble the full current state for *session_id*, or None when the
    session has nothing running (Fixes #291).

    Read-only over the same records the dashboard's liveness reader uses —
    plan status files, task and job ``state.json`` — plus the session file and
    the audit tail for "what the last event was and when". A torn or corrupt
    read is skipped, never fatal: a snapshot missing one job still carries the
    truth about the rest.
    """
    root = Path(project_root)
    session = _read_json(resolve_home() / "sessions" / f"{session_id}.json")
    plans = _collect_plans(root)
    tasks = _collect_runs(root / ".snodo" / "tasks")
    jobs = _collect_runs(root / ".snodo" / "jobs", job_dirs=True)
    last_event = _last_audit_event(root / ".snodo" / "audit.log")

    if not _anything_running(plans, tasks, jobs):
        return None

    project_id = ""
    run_started_at = None
    if isinstance(session, dict):
        project_id = str(session.get("project_id") or "")
        run_started_at = session.get("created_at")
    if run_started_at is None:
        starts = [r["started_at"] for r in tasks + jobs if r.get("started_at")]
        run_started_at = min(starts) if starts else None
    run_started_at = _as_iso(run_started_at)
    snapshot: dict = {
        "session_id": session_id,
        "project_id": project_id,
        "scope": scope_for_project_id(project_id) if project_id else "",
        "display_name": Path(project_root).name if project_root else "",
        "run_started_at": run_started_at,
        "plans": plans,
        "tasks": tasks,
        "jobs": jobs,
        "last_event": last_event,
        "snapshot_at": _now_iso(),
    }
    return snapshot


def _read_json(path: Path) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
        _logger.debug("liveness: %s unreadable: %s", path, exc)
        return None
    return data if isinstance(data, dict) else None


def root_obj(root: Path) -> Path:
    """The snodo home the session files live under (indirection for tests)."""
    return resolve_home()


def _as_iso(value: Any) -> Any:
    """Epoch numbers become ISO strings; anything else passes through."""
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC).isoformat()
    return value


def _collect_plans(root: Path) -> list:
    """One entry per plan that has begun, with its tasks' existing statuses.

    A plan whose status file has no entries yet, or only ``pending`` tasks,
    has not started and is left out: this payload answers "what is running",
    and the planner's ``pending`` default is not an answer.
    """
    plans_dir = root / ".snodo" / "plans"
    if not plans_dir.is_dir():
        return []
    plans: list = []
    for plan_path in sorted(plans_dir.iterdir()):
        if not plan_path.is_dir():
            continue
        status = _read_json(plan_path / "status.json")
        if not status:
            continue
        tasks = []
        for task_id, entry in (status.get("tasks") or {}).items():
            task_status = entry if isinstance(entry, str) else \
                (entry.get("status", "unknown") if isinstance(entry, dict) else "unknown")
            tasks.append({"id": task_id, "status": task_status})
        started = [t for t in tasks if t["status"] != _PENDING_TASK_STATUS]
        if started:
            plans.append({"name": plan_path.name, "tasks": started})
    return plans


def _collect_runs(runs_dir: Path, job_dirs: bool = False) -> list:
    """Task or job records: id, status, started_at — nothing more.

    ``description`` (the spec), ``halt`` payloads and ``usage`` stay on the
    disk: the emit contract refuses payloads and prompts on the wire, and
    these fields carry them.
    """
    if not runs_dir.is_dir():
        return []
    rows: list = []
    for entry in sorted(runs_dir.iterdir()):
        if not entry.is_dir():
            continue
        if job_dirs and not entry.name.startswith("j_"):
            continue
        state = _read_json(entry / "state.json")
        if state is None:
            continue
        rows.append({
            "id": entry.name,
            "status": str(state.get("status") or "unknown"),
            "started_at": _as_iso(state.get("started_at")),
        })
    return rows


def _last_audit_event(audit_path: Path) -> Optional[dict]:
    """The last well-formed audit line: its event_type and timestamp."""
    try:
        with open(audit_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _TAIL_BYTES))
            raw = f.read()
    except (FileNotFoundError, OSError):
        return None
    lines = raw.decode("utf-8", errors="replace").splitlines()
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("event_type"):
            return {
                "event_type": str(event["event_type"]),
                "timestamp": str(event.get("timestamp") or ""),
            }
    return None


def _anything_running(plans: list, tasks: list, jobs: list) -> bool:
    """True when the session has a state worth publishing.

    A plan that started or a task/job record: a session with nothing running
    has no liveness to carry and must not push at all (Fixes #291). Terminal
    statuses count — "the run finished" is a true snapshot — but the
    *absence* of any record is not. The audit tail is deliberately not a
    gate: it is project history, and yesterday's events must not make a
    today-idle session look like it has something to report.
    """
    return bool(plans or tasks or jobs)


# ---------------------------------------------------------------------------
# Audit-driven triggering
# ---------------------------------------------------------------------------


def _on_audit_event(event: Any, audit_log: Any) -> None:
    """Audit listener: push when a transition that changes *what is running*
    is appended in this process (Fixes #291).

    Inert until :func:`install` arms it: the registration is process-global,
    and a utility that appends one audit event (``snodo audit verify``, a
    dashboard refresh writing a diagnostic) is not a run and must not start
    network chatter. Cheap by contract once armed: membership test, throttle
    check, thread start. The append path wraps listener failures, so nothing
    raised here can break the attestation.
    """
    try:
        if not _ARMED:
            return
        event_type = getattr(event, "event_type", "")
        if event_type not in TRIGGER_EVENTS:
            return
        data = getattr(event, "data", None)
        data = data if isinstance(data, dict) else {}
        session_id = data.get("session_id")
        project_root = _project_root_from_log(audit_log)
        if not project_root:
            return
        if not session_id:
            session_id = _active_session_id(project_root)
        if not session_id:
            return
        request_liveness_push(
            str(session_id), project_root,
            force=event_type in TERMINAL_EVENTS,
        )
    except Exception as exc:  # noqa: BLE001 — an observer never breaks an append
        _logger.debug("liveness audit listener skipped: %s", exc)


def _project_root_from_log(audit_log: Any) -> str:
    """Derive the project root from an audit log path (``<root>/.snodo/audit.log``)."""
    log_path = getattr(audit_log, "log_path", None)
    if not log_path:
        return ""
    parent = Path(log_path).parent
    if parent.name == ".snodo":
        return str(parent.parent)
    return ""


def _active_session_id(project_root: str) -> str:
    """The active-session pointer for *project_root*, preferring the current mode."""
    try:
        from snodo.infrastructure.state import read_state
        state = read_state(project_root)
        pointers = state.active_session or {}
        if not pointers:
            return ""
        sid = pointers.get(state.current_mode)
        return sid or sorted(pointers.values())[0]
    except Exception as exc:  # noqa: BLE001 — never fatal: a push is opportunistic
        _logger.debug("liveness: no active session for %s: %s", project_root, exc)
        return ""


_INSTALLED = False
_ARMED = False
_INSTALL_LOCK = threading.Lock()


def note_transition(
    project_root: str,
    session_id: Optional[str] = None,
    force: bool = False,
) -> None:
    """Explicit liveness-change seam for writers outside the audit path.

    A plan/task status write changes *what is running* without necessarily
    appending an audit event, so whoever writes it says so here. Resolves the
    session from the active pointer when the caller does not name one, and
    never raises: liveness reporting is opportunistic, its failure must not
    surface through the thing that was merely reporting on.
    """
    try:
        if not _sync_gate_open():
            return
        sid = session_id or _active_session_id(str(project_root))
        if sid:
            request_liveness_push(sid, str(project_root), force=force)
    except Exception as exc:  # noqa: BLE001 — never fatal
        _logger.debug("liveness: note_transition skipped: %s", exc)


def install() -> None:
    """Arm liveness for this process (Fixes #291).

    Registers the audit listener (once — it is a process-global registry) and
    arms it, so engine transitions appended here start pushes. Every push
    still passes the sync gate, so with ``cloud.sync_enabled`` off the
    listener returns without any network call, anywhere. Idempotent.
    """
    global _INSTALLED, _ARMED
    with _INSTALL_LOCK:
        if not _INSTALLED:
            from snodo.infrastructure.audit import register_event_listener
            register_event_listener(_on_audit_event)
            _INSTALLED = True
        _ARMED = True


def uninstall() -> None:
    """Disarm the audit listener. Test seam; a run never disarms mid-flight."""
    global _ARMED
    _ARMED = False
