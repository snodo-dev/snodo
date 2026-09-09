"""Lock-free liveness facts for the dashboard.

FILE: snodo/dashboard/liveness.py

The engine records everything an operator needs to tell a slow phase from a
dead process, and this module only reads it:

- ``.snodo/audit.log`` — one JSON line per event, appended under
  ``fcntl.flock`` as phase transitions happen (``dispatch``, ``validate``,
  ``post_validation_route``, ``halt``, ``transition``, ...) with ``task_ref``
  and an ISO timestamp.
- ``.snodo/tasks/<task_id>/state.json`` and ``.snodo/jobs/<job_id>/state.json``
  — each carries a ``usage`` list appended per LLM call by
  ``usage_tracker._append_usage`` (model, role, cost, duration_ms), plus
  ``pid``/``status``/``started_at`` for liveness.

The reader is deliberately lock-free: files are opened for plain reads only,
so a concurrent append under ``flock`` is never blocked by a watching
dashboard. A partially written state file or a torn trailing audit line is a
normal condition of reading a file another process is appending to — both are
skipped and reported, never fatal. Nothing here mutates, prunes or repairs.

Honesty constraint (ADR 034): per-turn telemetry does not exist for in-place
subprocess coders; their usage record appears only when the subprocess
returns. During that window elapsed time is the entire signal, and the pane
presents it as such rather than implying progress it cannot see.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional, Tuple

_logger = logging.getLogger(__name__)

#: Audit event types that mark a phase boundary an operator cares about.
#: Anything else in the log is chain noise for the live view.
_PHASE_EVENTS = {
    "dispatch",
    "validate",
    "post_validation_route",
    "token_consumed",
    "head_not_moved",
    "coder_timed_out",
    "subtask_spawned",
    "halt",
    "transition",
    "task_complete",
    "verification_executed",
    "task_merged",
    "unverified_merge_blocked",
    "merge_conflict_escalated",
    "merge_failed_escalated",
}

#: Statuses that mean the run is done; anything else is treated as live.
_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "unmerged", "blocked"}

#: Statuses that mean the run is waiting to start, not running. A queued job
#: has no pid and no markers yet; it is not stale, it simply has not begun.
_WAITING_STATUSES = {"queued", "pending"}

#: Hard cap on the audit tail read per refresh — the dashboard must never
#: load an unbounded log into memory on a timer.
_AUDIT_TAIL_BYTES = 512 * 1024

#: Events that stop a task until a person acts. A task awaiting one of these
#: is the only thing on the cockpit that will not progress on its own: an
#: escalated disagreement or a merge conflict needs a human, and a halt whose
#: judges abstained — "could not reach a verdict" — has already spent its
#: budget and will retry forever without intervention. Operator actions are
#: taken in ``snodo cloud``/``snodo authorize``, never from the viewer.
_AWAITING_EVENTS = {
    "halt",
    "disagreement_escalated",
    "merge_conflict_escalated",
    "merge_failed_escalated",
    "unverified_merge_blocked",
}

#: Events that mean a task got past a waiting state: it completed, merged, or
#: (a later dispatch/transition) the operator already acted on it.
_RESOLVED_EVENTS = {
    "task_complete",
    "task_merged",
    "dispatch",
    "transition",
    "subtask_spawned",
}

#: Bounded number of raw audit events kept on the snapshot for the viewer's
#: Live Log, so a refresh performs exactly one tail read, never a second.
_AUDIT_TAIL_KEEP = 40


def _halt_event_fields(event: dict) -> Tuple[str, str]:
    """Return ``(outcome_label, awaits_text)`` for a halt/escalation event.

    The recorded outcome is what groups a wave that failed the same way six
    times into one fact: ``halt_type``/``final_decision`` is the canonical
    vocabulary, and an abstention (a judge that could not reach a verdict) is
    named as such rather than folded into a generic blocker.
    """
    event_type = event.get("event_type", "")
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    reason = str(data.get("reason") or "")
    halt_type = str(
        data.get("final_decision") or data.get("halt_type") or ""
    ) or event_type.replace("_escalated", "").replace("unverified_merge_", "merge ")

    # An abstention — a judge that exhausted its budget without a verdict — is
    # the halt that will not resolve on its own. It is recorded across the
    # reason text and the validator/failure payloads, so scan them all rather
    # than trusting one field, and name it distinctly from a plain blocker.
    abstain_signal = "abstain" in reason.lower() or "abstain" in str(
        data.get("raw_halt_type", "")
    ).lower() or "abstain" in json.dumps(data, default=str).lower()
    if abstain_signal:
        return ("abstention", "a verdict the judges could not reach")
    if halt_type == "escalate" or event_type == "disagreement_escalated":
        return ("escalate", "authorization (snodo authorize)")
    if event_type == "merge_conflict_escalated":
        return ("merge_conflict", "a merge conflict to resolve")
    if event_type == "merge_failed_escalated" or event_type == "unverified_merge_blocked":
        return ("merge_failed", "a failed/unverified merge to resolve")
    if halt_type in ("validator_error", "internal_error"):
        return (halt_type, f"a {halt_type} to investigate")
    if halt_type == "blocker" or event_type == "halt":
        return ("blocker", "a blocker to fix and re-run")
    detail = (reason or halt_type or event_type).strip()
    return (detail or "halted", detail or "a decision")


def attention_analysis(events: List[dict], now: float) -> dict:
    """Group the bounded audit tail into what a human must act on.

    Pure analysis of events already read by :func:`tail_audit_events` — it
    takes no lock, reads no extra file, and its cost is bounded by the same
    tail window. Returns:

    - ``awaiting``: the most recent escalation/halt per task that has not since
      been resolved, each with what it awaits and how long it has waited;
    - ``halt_outcomes``: every escalation/halt grouped by its recorded outcome
      (so six identical failures read as one fact);
    - ``awaiting_count``: how many tasks await a person right now.
    """
    # task_ref -> (last awaiting event ts, its dict) and last resolved event ts.
    last_awaiting: dict = {}
    last_resolved_ts: dict = {}
    outcome_counts: dict = {}

    for event in events:
        event_type = event.get("event_type", "")
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        task_ref = str(data.get("task_ref") or "")
        ts = _iso_to_epoch(event.get("timestamp"))
        if ts is None:
            continue

        if event_type in _AWAITING_EVENTS:
            outcome, _ = _halt_event_fields(event)
            outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
            if task_ref:
                prev = last_awaiting.get(task_ref)
                if prev is None or ts >= prev[0]:
                    last_awaiting[task_ref] = (ts, event)
        elif event_type in _RESOLVED_EVENTS and task_ref:
            if ts > last_resolved_ts.get(task_ref, float("-inf")):
                last_resolved_ts[task_ref] = ts

    awaiting: List[dict] = []
    for task_ref, (ts, event) in last_awaiting.items():
        if last_resolved_ts.get(task_ref, float("-inf")) > ts:
            continue  # the operator already acted; it is no longer waiting
        outcome, awaits = _halt_event_fields(event)
        awaiting.append({
            "task_ref": task_ref,
            "event_type": event.get("event_type", ""),
            "outcome": outcome,
            "awaits": awaits,
            "waiting_since": ts,
            "waited_seconds": max(0.0, now - ts),
        })

    # Longest-waiting first: the thing the operator has been ignoring longest.
    awaiting.sort(key=lambda a: a["waiting_since"])
    return {
        "awaiting": awaiting,
        "halt_outcomes": outcome_counts,
        "awaiting_count": len(awaiting),
    }


def cost_rollup(runs: List["RunRow"], task_rows: dict) -> dict:
    """Aggregate the per-call cost the usage records already carry.

    Nothing else in the cockpit totals cost across a project. The rollup sums
    each run's cost over the *whole* ``runs`` list (which the reader truncates
    to live + a few settled rows only for display). A background job that wraps
    a task records the same usage twice (dual-written); its cost is counted
    once, on the task row, so the total is a true project cost, not a doubled
    one.
    """
    total = 0.0
    partial = False
    runs_with_cost = 0
    for row in runs:
        if row.kind == "job" and row.linked_ref and row.linked_ref in task_rows:
            continue  # its cost is already counted on the task row
        if row.cost_total is None:
            continue
        total += row.cost_total
        runs_with_cost += 1
        partial = partial or row.cost_partial
    return {
        "total": total,
        "partial": partial,
        "runs_with_cost": runs_with_cost,
        "runs_total": len(runs),
    }


@dataclass
class PhaseMarker:
    """One timestamped sign of life, from the audit chain or a usage record."""

    ts: float
    source: str  # "audit" | "usage" | "state"
    label: str   # e.g. "dispatch", "validate:post_execute", "coder", "validator:quality"
    detail: str = ""


@dataclass
class RunRow:
    """One task or job, assembled from everything on disk about it."""

    kind: str  # "task" | "job"
    run_id: str
    description: str
    status: str
    pid: Optional[int]
    started_at: Optional[float]
    markers: List[PhaseMarker] = field(default_factory=list)
    usage_records: int = 0
    cost_total: Optional[float] = None
    cost_partial: bool = False
    tokens_total: int = 0
    coder_hint: str = ""
    linked_ref: str = ""
    state_readable: bool = True

    # -- derived views -------------------------------------------------

    def last_marker(self) -> Optional[PhaseMarker]:
        return max(self.markers, key=lambda m: m.ts) if self.markers else None

    def phase_group(self, now: float) -> Tuple[str, Optional[float]]:
        """Return ``(phase, since_ts)`` for the trailing run of same-phase markers.

        A marker names a transition; the phase an operator is *in* is what the
        transition started (a ``validate:pre_execute`` event means the engine
        is now executing). Consecutive markers that name the same phase are one
        phase, and it has been going since the first marker of the run.
        """
        if not self.markers:
            return ("unknown", self.started_at)
        ordered = sorted(self.markers, key=lambda m: m.ts)
        current = _phase_for(ordered[-1].label)
        since = ordered[-1].ts
        for marker in reversed(ordered):
            if _phase_for(marker.label) != current:
                break
            since = marker.ts
        return (current, since)

    def idle_seconds(self, now: float) -> Optional[float]:
        last = self.last_marker()
        return (now - last.ts) if last else None

    def is_terminal(self) -> bool:
        return self.status in _TERMINAL_STATUSES

    def alive(self) -> Optional[bool]:
        """True/False when a pid was recorded, None when liveness is unanswerable."""
        return pid_alive(self.pid)


def pid_alive(pid: Optional[int]) -> Optional[bool]:
    """Best-effort liveness probe — signal 0, never a real signal.

    Returns None when no pid was recorded (a run started before pids were
    recorded), True when the process exists, False when it does not.
    """
    if pid is None:
        return None
    try:
        os.kill(int(pid), 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just not ours to signal
    except (ValueError, OSError):
        return None
    return True


#: Marker label -> the phase the marker *started*. An audit event names a
#: transition; "validate:pre_execute" fired means the engine is now executing,
#: which is what an operator wants to read. Unknown labels pass through.
_PHASE_BY_MARKER = {
    "coder": "execute",
    "inplace_coder": "execute",
    "validate:pre_execute": "execute",
    "validate:post_execute": "route",
    "dispatch": "post-validate",
    "token_consumed": "execute",
    "head_not_moved": "execute",
    "coder_timed_out": "execute",
    "post_validation_route": "route",
    "subtask_spawned": "recovery",
    "halt": "halted",
    "transition": "transition",
    "task_complete": "complete",
    "verification_executed": "verify",
    "task_merged": "merged",
    "unverified_merge_blocked": "merge-blocked",
    "merge_conflict_escalated": "merge-conflict",
    "merge_failed_escalated": "merge-failed",
}


def _phase_for(label: str) -> str:
    """Map a marker label to the phase it started."""
    if label.startswith("validator:"):
        return "validate"
    return _PHASE_BY_MARKER.get(label, label)


# ---------------------------------------------------------------------------
# Lock-free readers
# ---------------------------------------------------------------------------


def read_json_state(path: Path) -> Optional[dict]:
    """Read a state.json without any locking; None when unreadable/partial.

    A concurrent writer replaces the file atomically (temp + ``os.replace``)
    but a read that lands mid-rename or mid-write can still see a partial
    document. That is a normal condition for a watcher: return None and let
    the caller render the previous-known or an "unreadable" row rather than
    crash the view.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        _logger.debug("dashboard: state file %s unreadable (partial write?): %s", path, e)
        return None
    return data if isinstance(data, dict) else None


def tail_audit_events(path: Path, max_bytes: int = _AUDIT_TAIL_BYTES) -> Tuple[List[dict], List[str]]:
    """Parse the tail of the audit log; returns ``(events, notes)``.

    Reads with a plain open() — no ``fcntl.flock`` is taken, so an append by
    a running engine is never blocked by this reader. The final line is
    dropped when it is not newline-terminated: the engine writes whole lines,
    so a torn tail is a mid-append snapshot, not a corrupt log. Malformed
    lines are counted and reported, never fatal.
    """
    events: List[dict] = []
    notes: List[str] = []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            window = min(size, max_bytes)
            f.seek(size - window)
            if window < size:
                # Drop the first (possibly partial) line of the window.
                f.readline()
            raw = f.read()
    except FileNotFoundError:
        return events, ["no audit log yet"]
    except OSError as e:
        return events, [f"audit log unreadable: {e}"]

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if raw and not raw.endswith(b"\n"):
        # Mid-append snapshot of the line currently being written.
        if lines:
            lines = lines[:-1]
        notes.append("tail line in-flight (skipped)")

    malformed = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(event, dict):
            events.append(event)
    if malformed:
        notes.append(f"{malformed} malformed line(s) skipped")
    return events, notes


# ---------------------------------------------------------------------------
# Snapshot collection
# ---------------------------------------------------------------------------


def _iso_to_epoch(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def _audit_marker(event: dict) -> Optional[PhaseMarker]:
    event_type = event.get("event_type", "")
    if event_type not in _PHASE_EVENTS:
        return None
    ts = _iso_to_epoch(event.get("timestamp"))
    if ts is None:
        return None
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    label = event_type
    if event_type == "validate" and data.get("phase"):
        label = f"validate:{data['phase']}"
    detail = str(data.get("decision") or data.get("outcome") or data.get("command") or "")
    return PhaseMarker(ts=ts, source="audit", label=label, detail=detail)


def _usage_markers(state: dict) -> List[PhaseMarker]:
    markers: List[PhaseMarker] = []
    usage = state.get("usage")
    if not isinstance(usage, list):
        return markers
    for record in usage:
        if not isinstance(record, dict):
            continue
        ts = _iso_to_epoch(record.get("timestamp"))
        if ts is None:
            continue
        role = str(record.get("role") or "llm")
        model = str(record.get("model") or "")
        detail = f"{model} {record.get('duration_ms', '')}ms".strip()
        markers.append(PhaseMarker(ts=ts, source="usage", label=role, detail=detail))
    return markers


def _summarize_usage(state: dict) -> Tuple[int, Optional[float], bool, int]:
    usage = state.get("usage")
    if not isinstance(usage, list):
        return (0, None, False, 0)
    count = 0
    total_cost: Optional[float] = None
    partial = False
    tokens = 0
    for record in usage:
        if not isinstance(record, dict):
            continue
        count += 1
        cost = record.get("cost")
        if cost is None:
            partial = True
        else:
            try:
                total_cost = (total_cost or 0.0) + float(cost)
            except (TypeError, ValueError):
                partial = True
        tok = record.get("total_tokens")
        if isinstance(tok, (int, float)):
            tokens += int(tok)
    return (count, total_cost, partial, tokens)


def _link_job_tasks(project_root: Path, jobs: dict) -> None:
    """Attach each job's task.json ids so audit markers can be merged in."""
    for job_id, info in jobs.items():
        task_state = read_json_state(project_root / ".snodo" / "jobs" / job_id / "task.json")
        if task_state:
            info["task_ref"] = task_state.get("task_id") or task_state.get("retry_task_id") or ""
            info["description"] = info["description"] or task_state.get("description", "")
            info["coder_hint"] = str(task_state.get("coder") or "")


def collect_snapshot(project_root: str, now: Optional[float] = None) -> dict:
    """Read everything on disk into a plain snapshot dict. Pure reads only."""
    if now is None:
        now = time.time()
    root = Path(project_root)
    notes: List[str] = []

    events, audit_notes = tail_audit_events(root / ".snodo" / "audit.log")
    notes.extend(audit_notes)

    by_task: dict = {}
    recent_events: List[dict] = []
    for event in events:
        marker = _audit_marker(event)
        if marker is None:
            continue
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        task_ref = str(data.get("task_ref") or "")
        if task_ref:
            by_task.setdefault(task_ref, []).append(marker)
        recent_events.append({
            "ts": marker.ts,
            "label": marker.label,
            "task_ref": task_ref,
            "detail": marker.detail,
            "sequence": event.get("sequence"),
        })
    recent_events.sort(key=lambda e: e["ts"])

    runs: List[RunRow] = []
    task_rows: dict = {}

    # -- tasks (foreground runs record a pid here as of this release) ----
    tasks_dir = root / ".snodo" / "tasks"
    if tasks_dir.is_dir():
        for entry in sorted(tasks_dir.iterdir()):
            if not entry.is_dir():
                continue
            state = read_json_state(entry / "state.json")
            if state is None:
                notes.append(f"task {entry.name}: state.json unreadable (partial write?)")
                runs.append(RunRow(
                    kind="task", run_id=entry.name, description="", status="unreadable",
                    pid=None, started_at=None, state_readable=False,
                ))
                continue
            markers = list(by_task.get(entry.name, []))
            markers.extend(_usage_markers(state))
            count, cost, partial, tokens = _summarize_usage(state)
            coder = ""
            for event in reversed(events):
                data = event.get("data") if isinstance(event.get("data"), dict) else {}
                if data.get("task_ref") == entry.name and data.get("coder"):
                    coder = str(data["coder"])
                    break
            runs.append(RunRow(
                kind="task",
                run_id=entry.name,
                description=str(state.get("description") or ""),
                status=str(state.get("status") or "unknown"),
                pid=state.get("pid") if isinstance(state.get("pid"), int) else None,
                started_at=_iso_to_epoch(state.get("started_at") or state.get("created_at")),
                markers=markers,
                usage_records=count,
                cost_total=cost,
                cost_partial=partial,
                tokens_total=tokens,
                coder_hint=coder,
            ))
            task_rows[entry.name] = runs[-1]

    # -- jobs (background runs: pid/status already recorded) --------------
    jobs_dir = root / ".snodo" / "jobs"
    jobs_info: dict = {}
    if jobs_dir.is_dir():
        for entry in sorted(jobs_dir.iterdir()):
            if not entry.is_dir() or not entry.name.startswith("j_"):
                continue
            state = read_json_state(entry / "state.json")
            if state is None:
                notes.append(f"job {entry.name}: state.json unreadable (partial write?)")
                runs.append(RunRow(
                    kind="job", run_id=entry.name, description="", status="unreadable",
                    pid=None, started_at=None, state_readable=False,
                ))
                continue
            jobs_info[entry.name] = {
                "description": "",
                "task_ref": "",
                "coder_hint": "",
                "state": state,
            }
    _link_job_tasks(root, jobs_info)
    for job_id, info in jobs_info.items():
        state = info["state"]
        markers = _usage_markers(state)
        task_ref = info.get("task_ref") or ""
        if task_ref:
            markers.extend(by_task.get(task_ref, []))
            # A background job's LLM calls are appended to both its job and its
            # task state.json by usage_tracker; say so once per linked job so
            # the identical costs on two rows are not read as a running total.
            if task_ref in task_rows:
                notes.append(
                    f"job {job_id} wraps task {task_ref}: its usage records are "
                    "dual-written, so each row is complete — do not add them"
                )
            # A task's own state.json is written by the same process, so prefer
            # its recorded coder name when the job hint is absent.
            linked = task_rows.get(task_ref)
            if linked is not None and not info.get("coder_hint"):
                info["coder_hint"] = linked.coder_hint
        count, cost, partial, tokens = _summarize_usage(state)
        runs.append(RunRow(
            kind="job",
            run_id=job_id,
            description=str(info.get("description") or ""),
            status=str(state.get("status") or "unknown"),
            pid=state.get("pid") if isinstance(state.get("pid"), int) else None,
            started_at=_iso_to_epoch(state.get("started_at") or state.get("created_at")),
            markers=markers,
            usage_records=count,
            cost_total=cost,
            cost_partial=partial,
            tokens_total=tokens,
            coder_hint=str(info.get("coder_hint") or ""),
            linked_ref=task_ref,
        ))

    live = [r for r in runs if not r.is_terminal()]
    settled = [r for r in runs if r.is_terminal()]
    settled.sort(key=lambda r: (r.started_at or 0.0), reverse=True)
    attention = attention_analysis(events, now)
    return {
        "project_root": str(root),
        "now": now,
        "runs": live + settled[:5],
        "runs_all": runs,
        "recent_events": recent_events[-12:],
        # Raw bounded tail for the viewer's audit pane and search: the same
        # window this reader already parsed, so a refresh never re-reads the log.
        "tail_events": events[-_AUDIT_TAIL_KEEP:],
        "attention": attention,
        "cost": cost_rollup(runs, task_rows),
        "notes": notes,
        "audit_events_seen": len(recent_events),
    }


# ---------------------------------------------------------------------------
# Formatting helpers (shared by the cockpit panes)
# ---------------------------------------------------------------------------


def fmt_age(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    seconds = max(0.0, seconds)
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, sec = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m{sec:02d}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h{minutes:02d}m"
    return f"{hours // 24}d{hours % 24:02d}h"


def idle_style(seconds: Optional[float]) -> str:
    if seconds is None:
        return "dim"
    if seconds <= 30:
        return "green"
    if seconds <= 180:
        return "yellow"
    return "bold red"


def fmt_cost(row: RunRow) -> str:
    if row.cost_total is None:
        return "unknown"
    prefix = "~" if row.cost_partial else ""
    return f"{prefix}${row.cost_total:.4f}"


def liveness_text(row: RunRow) -> str:
    alive = row.alive()
    if row.is_terminal():
        return "[dim]exited[/dim]" if alive is not True else "alive"
    if alive is None:
        return "[magenta]pid not recorded[/magenta]"
    return "[green]alive[/green]" if alive else "[bold red]DEAD[/bold red]"


def in_place_blind(row: RunRow, now: float) -> bool:
    """True when a coder phase is running blind — the disk cannot see progress.

    In-place subprocess coders emit their usage record only when the
    subprocess returns (ADR 034), so inside that window an elapsed audit
    signal is all there is. Say so rather than implying progress the view
    cannot see.
    """
    if row.is_terminal():
        return False
    phase, since = row.phase_group(now)
    if phase != "execute":
        return False
    signals = [m for m in row.markers if m.source == "usage" and m.ts >= since]
    return not signals


#: A record whose state says running but has had no sign of life for this long
#: is stale, not live. The engine appends an audit event or a usage record on
#: every phase transition and every LLM call, so a live run never goes silent
#: for this long; a finished run whose state.json was never updated does.
_STALE_AFTER_SECONDS = 600.0


def is_stale(
    row: RunRow,
    now: float,
) -> bool:
    """True when a record that claims to be running is actually stale.

    A task's ``state.json`` keeps ``status: running`` after its run ends, so
    days-old finished work would otherwise appear live. A record is current
    when either of these holds:

    - its pid is alive (a live process is the strongest signal — a foreground
      run records the engine's own pid at start, a background job the
      wrapper's),
    - it has had a sign of life (audit event or usage record) within the last
      ``_STALE_AFTER_SECONDS``.

    A record that meets neither is stale and must be named stale rather than
    presented as running. The active session's ``current_task`` is deliberately
    NOT a liveness signal: it is set at run start and never cleared, and the
    session's ``updated_at`` does not tick during a run, so it is exactly as
    stale-prone as the ``status: running`` field it would be asked to rescue.
    """
    if row.is_terminal():
        return False
    if row.status in _WAITING_STATUSES:
        return False
    if row.alive() is True:
        return False
    idle = row.idle_seconds(now)
    if idle is not None and idle < _STALE_AFTER_SECONDS:
        return False
    return True
