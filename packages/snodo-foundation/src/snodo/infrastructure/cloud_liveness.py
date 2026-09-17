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
- **The plan's shape, not its history.** Plans carry structure — plan, then
  waves, then tasks, then the jobs beneath them — and a branch that has
  completed is a count and a summary, not a re-shipment of its members. What
  is sent grows with the plan's shape and what is in flight, never with
  elapsed time (Fixes #303).
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
from collections import Counter
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Iterable, Optional
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

#: Statuses that mean a task or job record has settled. The same vocabulary
#: the CLI and the dashboard already treat as terminal (``task_cmd``'s
#: ``_TASK_TERMINAL_STATUSES``); nothing new. Used only to decide what is
#: detail (a live record, enumerated) and what is a settled branch (counted)
#: — never to rename a status on the wire (Fixes #303).
_SETTLED_RUN_STATUSES = frozenset({
    "completed", "failed", "cancelled", "unmerged", "blocked",
})

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

    The payload carries the plan's *shape* — plan, then waves, then tasks,
    then the jobs beneath them — the way `snodo plan status` renders it
    (Fixes #303). A branch that has completed is a count and a summary
    ("wave 1: 3/3 done"), not a list of its members; the running frontier
    keeps its detail, because that is the part a viewer is watching. What
    is sent grows with the plan's shape and what is in flight, never with
    elapsed time: a session's settled history is tallied, not re-shipped.

    Read-only over the same records the plan-status view reads — plan files,
    task and job ``state.json`` — plus the session file and the audit tail
    for "what the last event was and when". ``last_event`` is exactly that:
    the last decision recorded in the governance log, nothing more. When a
    status write moved without appending an audit event, the file's mtime
    still says so, and ``last_activity_at`` carries the newest of those
    marks: when something last happened, across everything this snapshot
    reports (Fixes #324). A torn or corrupt read is skipped,
    never fatal: a snapshot missing one job still carries the truth about the
    rest.
    """
    root = Path(project_root)
    session = _read_json(resolve_home() / "sessions" / f"{session_id}.json")
    task_rows = _collect_runs(root / ".snodo" / "tasks")
    job_rows = _collect_runs(root / ".snodo" / "jobs", job_dirs=True)
    plans, tallied_refs, detailed_refs, tallied_job_ids, nested_job_ids = \
        _collect_plans(root, task_rows, job_rows)
    last_event = _last_audit_event(root / ".snodo" / "audit.log")
    last_activity_at = _last_activity_at(root, last_event)

    if not _anything_running(plans, task_rows, job_rows):
        return None

    project_id = ""
    run_started_at = None
    if isinstance(session, dict):
        project_id = str(session.get("project_id") or "")
        run_started_at = session.get("created_at")
    if run_started_at is None:
        starts = [r["started_at"] for r in task_rows + job_rows if r.get("started_at")]
        run_started_at = min(starts) if starts else None
    run_started_at = _as_iso(run_started_at)

    # Records the plan structure already carries are not enumerated a second
    # time once settled; a record that is still live always stays enumerated —
    # at the plan frontier if the structure shows it there, at the top level
    # otherwise — so nothing running can be hidden by the collapsing
    # (Fixes #303).
    live_tasks, task_counts = _unreported(task_rows, tallied_refs, detailed_refs)
    live_jobs, job_counts = _unreported(job_rows, tallied_job_ids, nested_job_ids)
    snapshot: dict = {
        "session_id": session_id,
        "project_id": project_id,
        "scope": scope_for_project_id(project_id) if project_id else "",
        "display_name": Path(project_root).name if project_root else "",
        "run_started_at": run_started_at,
        "plans": plans,
        "tasks": live_tasks,
        "jobs": live_jobs,
        "task_status_counts": task_counts,
        "job_status_counts": job_counts,
        "last_event": last_event,
        "last_activity_at": last_activity_at,
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


def _task_entry_status(entry: Any) -> str:
    """The planner's status string for one status.json entry, verbatim."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        return str(entry.get("status", "unknown"))
    return "unknown"


def _status_counts(statuses: Iterable[str]) -> dict:
    """Per-status tally of existing status values, in a stable order."""
    return dict(sorted(Counter(statuses).items()))


def _plan_waves(plan_path: Path) -> Optional[list]:
    """The plan's wave structure from plan.yml; None when absent or unreadable.

    A plan with no readable plan.yml is not dropped — its task detail rides
    directly on the plan node — because a snapshot that loses *one*
    relationship still carries the truth about the rest (Fixes #303).
    """
    plan_file = plan_path / "plan.yml"
    if not plan_file.is_file():
        return None
    try:
        import yaml
        with open(plan_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as exc:  # noqa: BLE001 — an unreadable plan reads shapeless
        _logger.debug("liveness: %s unreadable: %s", plan_file, exc)
        return None
    waves = data.get("waves") if isinstance(data, dict) else None
    return waves if isinstance(waves, list) else None


def _collect_plans(
    root: Path,
    task_rows: list,
    job_rows: list,
) -> tuple:
    """One entry per plan that has begun, carrying the plan's shape.

    Returns ``(plan_nodes, tallied_refs, detailed_refs, tallied_job_ids,
    nested_job_ids)``: the task refs whose statuses the structure tallies,
    the refs it shows in detail, the settled jobs counted inside a plan node,
    and the live jobs nested under a task — everything the caller must not
    enumerate a second time at the top level.

    A plan whose status file has no entries yet, or only ``pending`` tasks,
    has not started and is left out: this payload answers "what is running",
    and the planner's ``pending`` default is not an answer. Inside a begun
    plan, a wave that has settled is a count and a summary; an unsettled wave
    keeps the detail of its started tasks and the live jobs beneath them
    (Fixes #303).
    """
    plans_dir = root / ".snodo" / "plans"
    if not plans_dir.is_dir():
        return [], set(), set(), set(), set()

    tasks_by_id = {r["id"]: r for r in task_rows}
    infos: list = []
    for plan_path in sorted(plans_dir.iterdir()):
        if not plan_path.is_dir():
            continue
        status = _read_json(plan_path / "status.json")
        if not status:
            continue
        statuses = {
            task_id: _task_entry_status(entry)
            for task_id, entry in (status.get("tasks") or {}).items()
        }
        waves = _plan_waves(plan_path)
        if waves:
            # plan.yml tasks with no status entry have not begun; counting
            # them as pending keeps "total" honest without inventing a status.
            for wave in waves:
                if isinstance(wave, dict):
                    for task_id in wave.get("tasks") or []:
                        statuses.setdefault(str(task_id), _PENDING_TASK_STATUS)
        if not any(s != _PENDING_TASK_STATUS for s in statuses.values()):
            continue
        infos.append({"name": plan_path.name, "statuses": statuses, "waves": waves})

    # Attribute each job to the first plan (sorted by name) declaring its
    # task. A settled job joins that plan's tally; a live job waits to be
    # nested under its task node, and surfaces at the top level if it never
    # gets one — a live record is never hidden by the collapsing.
    ref_owner: dict = {}
    for idx, info in enumerate(infos):
        for task_id in info["statuses"]:
            ref_owner.setdefault(task_id, idx)
    settled_job_ids: set = set()
    settled_job_counts: list = [Counter() for _ in infos]
    live_jobs_by_ref: list = [{} for _ in infos]
    for job in job_rows:
        idx = ref_owner.get(job.get("task_ref") or "")
        if idx is None:
            continue
        if job["status"] in _SETTLED_RUN_STATUSES:
            settled_job_ids.add(job["id"])
            settled_job_counts[idx][job["status"]] += 1
        else:
            live_jobs_by_ref[idx].setdefault(job["task_ref"], []).append(job)

    nodes: list = []
    nested_job_ids: set = set()
    detailed_refs: set = set()
    for idx, info in enumerate(infos):
        statuses = info["statuses"]
        node = {
            "name": info["name"],
            "total": len(statuses),
            "status_counts": _status_counts(statuses.values()),
        }
        if settled_job_counts[idx]:
            node["job_status_counts"] = dict(sorted(settled_job_counts[idx].items()))
        if all(s in TERMINAL_PLAN_STATUSES for s in statuses.values()):
            # The whole plan is a completed branch: counts and summary, no
            # member list. Its live stragglers stay at the top level.
            nodes.append(node)
            continue
        live_for_ref = live_jobs_by_ref[idx]

        def task_node(task_id: str) -> dict:
            if statuses[task_id] not in TERMINAL_PLAN_STATUSES:
                # The node itself represents a live task, so it claims the
                # engine's live record for that ref. A settled node claims
                # nothing live: a record still running under a task the
                # planner calls finished must surface, not vanish.
                detailed_refs.add(task_id)
            tnode = {"id": task_id, "status": statuses[task_id]}
            live = live_for_ref.get(task_id, [])
            started = tasks_by_id.get(task_id, {}).get("started_at")
            if not started:
                starts = [j["started_at"] for j in live if j.get("started_at")]
                started = min(starts) if starts else None
            if started:
                tnode["started_at"] = started
            if live:
                tnode["jobs"] = [
                    {k: j[k] for k in ("id", "status", "started_at") if k in j}
                    for j in live
                ]
                nested_job_ids.update(j["id"] for j in live)
            return tnode

        enumerated: set = set()
        if info["waves"] is not None:
            wave_nodes = []
            for wave in info["waves"]:
                if not isinstance(wave, dict):
                    continue
                members = []
                for task_id in wave.get("tasks") or []:
                    task_id = str(task_id)
                    if task_id in statuses and task_id not in enumerated:
                        enumerated.add(task_id)
                        members.append(task_id)
                wave_node = {
                    "id": wave.get("id"),
                    "total": len(members),
                    "status_counts": _status_counts(statuses[t] for t in members),
                }
                settled_wave = members and all(
                    statuses[t] in TERMINAL_PLAN_STATUSES for t in members
                )
                if not settled_wave:
                    detail = [t for t in members if statuses[t] != _PENDING_TASK_STATUS]
                    if detail:
                        wave_node["tasks"] = [task_node(t) for t in detail]
                wave_nodes.append(wave_node)
            node["waves"] = wave_nodes
        leftover = [
            t for t, s in statuses.items()
            if s != _PENDING_TASK_STATUS and t not in enumerated
        ]
        if leftover:
            node["tasks"] = [task_node(t) for t in leftover]
        nodes.append(node)

    # A settled record whose task ref is tallied by a plan node is not
    # enumerated a second time at the top level; a live record is claimed
    # only where the structure actually shows it (a task node, a nested
    # job). Anything still running that the plan collapsed away surfaces
    # top-level — the collapsing may reorder the picture, never hide it.
    started_refs = {
        task_id
        for info in infos
        for task_id, s in info["statuses"].items()
        if s != _PENDING_TASK_STATUS
    }
    return nodes, started_refs, detailed_refs, settled_job_ids, nested_job_ids


def _unreported(rows: list, settled_claimed: set, live_claimed: set) -> tuple:
    """Split top-level records into live detail and a settled tally.

    A settled record already tallied by the plan structure (*settled_claimed*)
    is left out; one still running (*rows*) is enumerated in full unless the
    structure already shows it (*live_claimed*). Nothing running may be
    reduced to a number, and nothing counted may be enumerated twice
    (Fixes #303).
    """
    live: list = []
    counts: Counter = Counter()
    for row in rows:
        if row["status"] in _SETTLED_RUN_STATUSES:
            if row["id"] not in settled_claimed:
                counts[row["status"]] += 1
            continue
        if row["id"] in live_claimed:
            continue
        wire = {k: row[k] for k in ("id", "status", "started_at") if k in row}
        if row.get("task_ref"):
            wire["task_ref"] = row["task_ref"]
        live.append(wire)
    return live, dict(sorted(counts.items()))


def _collect_runs(runs_dir: Path, job_dirs: bool = False) -> list:
    """Task or job records: id, status, started_at — nothing more.

    ``description`` (the spec), ``halt`` payloads and ``usage`` stay on the
    disk: the emit contract refuses payloads and prompts on the wire, and
    these fields carry them. A job directory also carries the identity of
    the task it ran (``task.json``), read here as an internal link for the
    snapshot's structure — only the task id travels, never the spec.
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
        row = {
            "id": entry.name,
            "status": str(state.get("status") or "unknown"),
            "started_at": _as_iso(state.get("started_at")),
        }
        if job_dirs:
            row["task_ref"] = _job_task_ref(entry)
        rows.append(row)
    return rows


def _job_task_ref(job_dir: Path) -> str:
    """The task identity a job ran, as ``JobManager`` resolves it; "" if none.

    A plan-run row owns no single task (``plan_name`` set), so it links to
    nothing, mirroring ``_summarize_job``. Only ids are read from task.json;
    the description — the task spec — is never touched.
    """
    task = _read_json(job_dir / "task.json") or {}
    if task.get("plan_name"):
        return ""
    return str(task.get("task_id") or task.get("retry_task_id") or "")


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


def _mtime_or_none(path: Path) -> Optional[float]:
    """A record file's mtime, or None when it cannot be read."""
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _iso_epoch(value: Any) -> Optional[float]:
    """Epoch seconds of an ISO timestamp string; None when absent or unparseable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.timestamp()


def _last_activity_at(root: Path, last_event: Optional[dict]) -> Optional[str]:
    """When something last happened in this session, across everything the
    snapshot reports — not only what the audit log recorded (Fixes #324).

    ``last_event`` answers "when was the last decision recorded". A plan or
    task status write changes what is running and appends no audit event, so
    the audit tail can sit hours behind the very change that woke the push.
    The evidence of a status write is already on disk — the mtime of the file
    the write rewrote — so the newest of the plan ``status.json`` and task/job
    ``state.json`` mtimes, and the audit tail's own timestamp, is when this
    session last did something. Nothing is stored to make the clock move: the
    same records the snapshot already reports carry it, and no audit event is
    appended here (the audit log stays the governance record it is).
    """
    snodo_dir = root / ".snodo"
    epochs: list = []
    for runs_dir, job_dirs in (
        (snodo_dir / "tasks", False), (snodo_dir / "jobs", True),
    ):
        if runs_dir.is_dir():
            for entry in runs_dir.iterdir():
                if not entry.is_dir():
                    continue
                if job_dirs and not entry.name.startswith("j_"):
                    continue
                epoch = _mtime_or_none(entry / "state.json")
                if epoch is not None:
                    epochs.append(epoch)
    plans_dir = snodo_dir / "plans"
    if plans_dir.is_dir():
        for plan_path in plans_dir.iterdir():
            epoch = _mtime_or_none(plan_path / "status.json")
            if epoch is not None:
                epochs.append(epoch)
    audit_epoch = _iso_epoch((last_event or {}).get("timestamp"))
    if audit_epoch is not None:
        epochs.append(audit_epoch)
    if not epochs:
        return None
    return datetime.fromtimestamp(max(epochs), UTC).isoformat()


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
