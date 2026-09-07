"""snodo monitor — read-only live view of what the engine is already writing.

FILE: snodo/cli/commands/monitor_cmd.py

The engine records its own progress on disk as it runs:

- ``.snodo/audit.log`` — one JSON line per event, appended under
  ``fcntl.flock`` as phase transitions happen (``dispatch``, ``validate``,
  ``post_validation_route``, ``halt``, ``transition``, …) with ``task_ref``
  and an ISO timestamp.
- ``.snodo/tasks/<task_id>/state.json`` and ``.snodo/jobs/<job_id>/state.json``
  — each carries a ``usage`` list appended per LLM call by
  ``usage_tracker._append_usage`` (model, role, tokens, cost, duration_ms),
  plus ``pid``/``status``/``started_at`` for liveness.

This command renders those sources; it never writes to them. The reader is
deliberately lock-free: it opens files for plain reads only, so a concurrent
append under ``flock`` is never blocked by a watching monitor. A partially
written state file or a torn trailing audit line is a normal condition of
reading a file another process is appending to — both are skipped and
reported, never fatal.

Honesty constraint (ADR 034): per-turn telemetry does not exist for in-place
subprocess coders; their usage record appears only when the subprocess
returns. During that window elapsed time is the entire signal, and the view
presents it as such rather than implying progress it cannot see.
"""

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, List, Optional

import typer

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

#: Hard cap on the audit tail read per refresh — the monitor must never
#: load an unbounded log into memory on a timer.
_AUDIT_TAIL_BYTES = 512 * 1024


# ---------------------------------------------------------------------------
# Data model (plain types; rendering is separate so the collector is testable)
# ---------------------------------------------------------------------------


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

    def phase_group(self, now: float) -> tuple:
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
        _logger.debug("monitor: state file %s unreadable (partial write?): %s", path, e)
        return None
    return data if isinstance(data, dict) else None


def tail_audit_events(path: Path, max_bytes: int = _AUDIT_TAIL_BYTES) -> tuple:
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


def _summarize_usage(state: dict) -> tuple:
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

    # Worktrees say what is checked out right now.
    worktrees = _read_worktrees(root)

    live = [r for r in runs if not r.is_terminal()]
    settled = [r for r in runs if r.is_terminal()]
    settled.sort(key=lambda r: (r.started_at or 0.0), reverse=True)
    return {
        "project_root": str(root),
        "now": now,
        "runs": live + settled[:5],
        "recent_events": recent_events[-12:],
        "worktrees": worktrees,
        "notes": notes,
        "audit_events_seen": len(recent_events),
    }


def _read_worktrees(project_root: Path) -> List[dict]:
    """List the task worktrees under the configured container (read-only)."""
    from snodo.infrastructure.worktree import worktree_dir

    container = worktree_dir(str(project_root))
    out: List[dict] = []
    if not container.is_dir():
        return out
    try:
        entries = sorted(
            (p for p in container.iterdir() if p.is_dir() and not p.name.startswith(".")),
            key=_entry_mtime,
        )
    except OSError:
        return out
    for entry in entries:
        out.append({"task_id": entry.name, "path": str(entry), "branch": _worktree_branch(entry)})
    return out


def _entry_mtime(path: Path) -> float:
    """mtime that survives a worktree disappearing mid-listing."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _worktree_branch(worktree: Path) -> str:
    """Resolve the checked-out branch by reading files — never run git here.

    A linked worktree's ``.git`` is a file (``gitdir: ...``); the branch is in
    that gitdir's ``HEAD``. Every step is a plain read; any failure just means
    the branch is unknown to the view.
    """
    try:
        git_pointer = (worktree / ".git").read_text().strip()
    except OSError:
        return ""
    if not git_pointer.startswith("gitdir:"):
        return ""
    gitdir = git_pointer.split(":", 1)[1].strip()
    if not os.path.isabs(gitdir):
        gitdir = str((worktree / gitdir).resolve())
    try:
        head = Path(gitdir, "HEAD").read_text().strip()
    except OSError:
        return ""
    if head.startswith("ref:"):
        return head.split("/", 2)[-1]
    return head[:12]


# ---------------------------------------------------------------------------
# Formatting + rendering (rich)
# ---------------------------------------------------------------------------


def _fmt_age(seconds: Optional[float]) -> str:
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


def _idle_style(seconds: Optional[float]) -> str:
    if seconds is None:
        return "dim"
    if seconds <= 30:
        return "green"
    if seconds <= 180:
        return "yellow"
    return "bold red"


def _fmt_cost(row: RunRow) -> str:
    if row.cost_total is None:
        return "unknown"
    prefix = "~" if row.cost_partial else ""
    return f"{prefix}${row.cost_total:.4f}"


def _liveness_text(row: RunRow) -> str:
    alive = row.alive()
    if row.is_terminal():
        return "[dim]exited[/dim]" if alive is not True else "alive"
    if alive is None:
        return "[magenta]pid not recorded[/magenta]"
    return "[green]alive[/green]" if alive else "[bold red]DEAD[/bold red]"


def build_renderable(snapshot: dict):
    """Compose the rich renderable for one frame."""
    from rich.console import Group
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    now = snapshot["now"]

    header = Text()
    header.append("snodo monitor ", style="bold cyan")
    header.append(f"· {snapshot['project_root']} · read-only · ")
    header.append(f"{datetime.fromtimestamp(now):%H:%M:%S}", style="dim")
    header.append(
        "\nPHASE = the phase the last recorded transition started; "
        "SINCE LAST SIGN OF LIFE = audit event or LLM-call usage record, "
        "whichever is newer — the number that separates a slow phase from a dead run.",
        style="dim",
    )
    header.append("\n")

    runs_table = Table(title=None, expand=True, title_style="bold")
    runs_table.add_column("RUN", overflow="fold", max_width=26)
    runs_table.add_column("KIND")
    runs_table.add_column("PHASE", max_width=22)
    runs_table.add_column("PHASE FOR", justify="right")
    runs_table.add_column("SINCE LAST SIGN OF LIFE", justify="right")
    runs_table.add_column("ALIVE?")
    runs_table.add_column("CALLS", justify="right")
    runs_table.add_column("COST", justify="right")
    runs_table.add_column("DESC", overflow="ellipsis", max_width=34)

    rows = snapshot["runs"]
    blind_rows = []
    if not rows:
        runs_table.add_row("[dim](no task or job state on disk yet)[/dim]", *[""] * 8)
    for row in rows:
        if not row.state_readable:
            runs_table.add_row(
                row.run_id, row.kind, "[red]state unreadable[/red]", "—", "—", "—", "—", "—", "",
            )
            continue
        phase, since = row.phase_group(now)
        idle = row.idle_seconds(now)
        idle_str = _fmt_age(idle)
        phase_for = _fmt_age(now - since) if since else "—"
        desc = row.description
        if row.coder_hint:
            desc = f"{desc}  [coder: {row.coder_hint}]" if desc else f"[coder: {row.coder_hint}]"
        if _in_place_blind(row, now):
            blind_rows.append(row)
            phase = f"{phase} [dim](blind: ADR 034)[/dim]"
        kind_cell = f"{row.kind}·{row.status}"
        if row.linked_ref:
            kind_cell += f"\n[dim]→ {row.linked_ref}[/dim]"
        runs_table.add_row(
            Text(row.run_id, style="bold" if not row.is_terminal() else "dim"),
            kind_cell,
            phase,
            phase_for,
            f"[{_idle_style(idle)}]{idle_str}[/]",
            _liveness_text(row),
            str(row.usage_records),
            _fmt_cost(row),
            desc[:80],
        )

    events_table = Table(title="recent phase events", expand=True, title_style="bold")
    events_table.add_column("SEQ", justify="right", style="dim")
    events_table.add_column("TIME", style="dim")
    events_table.add_column("EVENT", max_width=24)
    events_table.add_column("TASK", overflow="fold", max_width=26)
    events_table.add_column("", overflow="ellipsis", max_width=40)
    for event in snapshot["recent_events"]:
        events_table.add_row(
            str(event.get("sequence", "")),
            f"{datetime.fromtimestamp(event['ts']):%H:%M:%S}",
            event["label"],
            str(event.get("task_ref") or ""),
            str(event.get("detail") or ""),
        )
    if not snapshot["recent_events"]:
        events_table.add_row("", "", "[dim](no phase events on the audit tail)[/dim]", "", "")

    parts = [header, runs_table, events_table]

    worktrees = snapshot["worktrees"]
    if worktrees:
        wt_table = Table(title="worktrees (what is checked out)", title_style="bold", expand=True)
        wt_table.add_column("TASK", max_width=26)
        wt_table.add_column("BRANCH", max_width=34)
        wt_table.add_column("PATH", overflow="fold")
        for wt in worktrees[-8:]:
            wt_table.add_row(wt["task_id"], wt["branch"] or "?", wt["path"])
        parts.append(wt_table)

    footer_lines = []
    for row in blind_rows[:3]:
        idle = row.idle_seconds(now)
        footer_lines.append(
            f"[yellow]{row.run_id}[/yellow]: in the coder phase with no per-call record yet "
            f"(idle {_fmt_age(idle)}). In-place subprocess coders emit usage only when the "
            "subprocess returns — ADR 034 records that as a decision, not a gap — so "
            "elapsed time is the whole signal here. Blind is not dead; check ALIVE?."
        )
    for note in snapshot["notes"][:6]:
        footer_lines.append(f"[dim]{note}[/dim]")
    if footer_lines:
        parts.append(Panel("\n".join(footer_lines), border_style="yellow", title="honesty"))

    return Group(*parts)


def _in_place_blind(row: RunRow, now: float) -> bool:
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


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


def register(app: typer.Typer) -> None:
    """Register top-level CLI commands onto app (called by discovery loop)."""

    @app.command()
    def monitor(
        interval: float = typer.Option(
            2.0, "--interval", "-i", help="Refresh interval in seconds",
        ),
        once: bool = typer.Option(
            False, "--once", help="Render a single frame and exit (for scripts/CI)",
        ),
        json_out: bool = typer.Option(
            False, "--json", help="Emit the snapshot as JSON instead of a rendered view",
        ),
    ):
        """Read-only live view of running tasks and jobs (renders disk state; writes nothing)."""
        return monitor_command(SimpleNamespace(
            interval=interval, once=once, json=json_out,
        ))


def monitor_command(args) -> int:
    """Render the live view, refreshing on a timer."""
    from snodo.infrastructure.paths import require_project_root

    project_root = require_project_root()
    interval = max(0.5, float(getattr(args, "interval", 2.0) or 2.0))
    once = bool(getattr(args, "once", False))
    json_out = bool(getattr(args, "json", False))

    snapshot = collect_snapshot(project_root)

    if json_out:
        from snodo.cli.json_output import emit_json, schema_name

        payload = dict(snapshot)
        payload["schema"] = schema_name("monitor")
        payload["ok"] = True
        payload["runs"] = [
            {
                "kind": r.kind, "run_id": r.run_id, "status": r.status, "pid": r.pid,
                "alive": r.alive(), "phase": r.phase_group(snapshot["now"])[0],
                "idle_seconds": r.idle_seconds(snapshot["now"]),
                "usage_records": r.usage_records, "cost_total": r.cost_total,
                "cost_partial": r.cost_partial, "tokens_total": r.tokens_total,
                "description": r.description, "coder": r.coder_hint,
                "state_readable": r.state_readable,
            }
            for r in snapshot["runs"]
        ]
        return emit_json(payload)

    if once:
        from rich.console import Console

        Console().print(build_renderable(snapshot))
        return 0

    from rich.console import Console
    from rich.live import Live

    console = Console()
    try:
        with Live(build_renderable(snapshot), console=console, transient=False) as live:
            while True:
                time.sleep(interval)
                live.update(build_renderable(collect_snapshot(project_root)))
    except KeyboardInterrupt:
        print()
        return 0
    except OSError as e:  # console vanished (detached terminal) — stop quietly
        print(f"monitor stopped: {e}", file=sys.stderr)
        return 0
    return 0
