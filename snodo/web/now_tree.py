"""Read-only, last-24-hour activity tree for one Snodo project."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from snodo.dashboard.providers import DashboardDataProvider
from snodo.web.recon_runs import project_recon_runs

WINDOW_SECONDS = 24 * 60 * 60


def _timestamp(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.timestamp()
        except ValueError:
            return None
    return None


def _now_timestamp(now: datetime | float) -> float:
    if isinstance(now, datetime):
        return (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).timestamp()
    return float(now)


def project_now_tree(project_path: str | Path, now: datetime | float) -> dict[str, Any]:
    """Calculate recent plan/wave/task activity without changing project files."""
    root = Path(project_path)
    now_ts = _now_timestamp(now)
    cutoff = now_ts - WINDOW_SECONDS
    empty: dict[str, Any] = {
        "liveness": "no_recent_signal", "last_activity": None, "plans": [], "outside_plan": []
    }
    try:
        if not root.is_dir() or not (root / ".snodo").is_dir():
            return empty
        provider = DashboardDataProvider(str(root))
        plans = provider.get_plans()
        tasks = provider.get_tasks("")
        jobs = provider.get_jobs("")
        plans_dir = root / ".snodo" / "plans"
        plan_dirs = {path.name: path for path in plans_dir.iterdir() if path.is_dir()} if plans_dir.exists() else {}

        # Preflight the records because the recon reader intentionally skips
        # individual unreadable files. In the tree, unreadable recon storage
        # must leave the established plan/task/job result untouched.
        recon_records: list[dict[str, Any]] = []
        recon_dir = root / ".snodo" / "recons"
        try:
            if recon_dir.exists():
                for run_dir in recon_dir.iterdir():
                    if run_dir.is_dir() and (run_dir / "state.json").exists():
                        (run_dir / "state.json").read_bytes()
                recon_records = project_recon_runs(root, now)
        except OSError:
            recon_records = []

        jobs_by_ref: dict[str, list[dict[str, Any]]] = {}
        job_signal: dict[str, float] = {}
        for job in jobs:
            ref = str(job.get("task_ref") or "")
            stamps = [_timestamp(job.get(key)) for key in ("created_at", "started_at", "completed_at")]
            stamps = [stamp for stamp in stamps if stamp is not None]
            if ref:
                jobs_by_ref.setdefault(ref, []).append(job)
                if stamps:
                    job_signal[ref] = max(job_signal.get(ref, 0), max(stamps))

        tasks_by_plan: dict[str, list[dict[str, Any]]] = {}
        for task in tasks:
            tasks_by_plan.setdefault(str(task.get("plan_name") or ""), []).append(task)

        recent_plans = []
        project_signals: list[float] = []
        for plan in plans:
            name = str(plan.get("name") or "")
            plan_dir = plan_dirs.get(name)
            status_file = plan_dir / "status.json" if plan_dir else None
            file_stamp = None
            if status_file is not None:
                try:
                    file_stamp = status_file.stat().st_mtime
                except OSError:
                    pass
            owned_tasks = tasks_by_plan.get(name, [])
            task_signal: dict[str, float] = {}
            for task in owned_tasks:
                tid = str(task.get("task_id") or "")
                ref = str(task.get("task_ref") or "")
                value = job_signal.get(tid, job_signal.get(ref))
                if value is not None:
                    task_signal[tid] = value
            last = max([v for v in [file_stamp, *task_signal.values()] if v is not None], default=None)
            if last is None or last < cutoff:
                continue
            project_signals.append(last)
            task_by_id = {str(t.get("task_id")): t for t in owned_tasks}
            waves = []
            for index, wave in enumerate(plan.get("waves") or [], 1):
                wave_tasks = []
                counts: dict[str, int] = {}
                for task_id in wave.get("tasks", []):
                    task_id = str(task_id)
                    task = task_by_id.get(task_id, {"task_id": task_id, "status": plan.get("tasks", {}).get(task_id, "pending")})
                    status = task.get("status", "unknown")
                    counts[status] = counts.get(status, 0) + 1
                    candidates = jobs_by_ref.get(task_id, []) + jobs_by_ref.get(str(task.get("task_ref") or ""), [])
                    running = next((job for job in reversed(candidates) if job.get("status") == "running"), None)
                    stamp = _timestamp(running.get("started_at")) if running else task_signal.get(task_id)
                    wave_tasks.append({
                        "task_id": task_id, "status": status,
                        "job_id": running.get("job_id") if running else None,
                        "elapsed_seconds": max(0, int(now_ts - stamp)) if stamp is not None else None,
                    })
                waves.append({"number": index, "task_total": len(wave_tasks), "status_counts": counts, "tasks": wave_tasks})
            plan_tasks = plan.get("tasks") or {}
            current_wave = next((i for i, wave in enumerate(plan.get("waves") or [], 1)
                                 if any(plan_tasks.get(str(t), "pending") != "completed" for t in wave.get("tasks", []))),
                                len(waves) if waves else 0)
            recent_plans.append({"name": name, "current_wave": current_wave, "total_waves": len(waves),
                                 "last_activity": last, "waves": waves})

        outside = []
        planned_ids = {str(task.get("task_id") or "") for task in tasks}
        outside_ids = {str(task.get("task_id") or "") for task in tasks_by_plan.get("", [])}
        outside_ids.update(ref for ref in jobs_by_ref if ref and ref not in planned_ids and ":" not in ref)
        for tid in outside_ids:
            candidates = jobs_by_ref.get(tid, [])
            recent = [j for j in candidates if any((s := _timestamp(j.get(k))) is not None and s >= cutoff
                                                   for k in ("created_at", "started_at", "completed_at"))]
            for job in recent:
                project_signals.extend(s for k in ("created_at", "started_at", "completed_at")
                                       if (s := _timestamp(job.get(k))) is not None)
            if not recent:
                continue
            job = next((j for j in reversed(recent) if j.get("status") == "running"), recent[-1])
            start = _timestamp(job.get("started_at"))
            outside.append({"task_id": tid, "status": job.get("status"), "job_id": job.get("job_id"),
                            "elapsed_seconds": max(0, int(now_ts - start)) if start is not None else None})

        recent_plans.sort(key=lambda row: row["last_activity"], reverse=True)
        for recon in recon_records:
            created = _timestamp(recon.get("created_at"))
            completed = _timestamp(recon.get("completed_at"))
            if created is not None and created >= cutoff:
                project_signals.append(created)
            if completed is not None and completed >= cutoff:
                project_signals.append(completed)

        project_last = max(project_signals, default=None)
        working = (any(t.get("status") == "in_progress" for t in tasks)
                   or any(j.get("status") == "running" for j in jobs)
                   or any(recon.get("state") == "running" for recon in recon_records))
        liveness = "working" if working else "reporting_not_progressing" if project_last is not None else "no_recent_signal"
        return {"liveness": liveness, "last_activity": project_last, "plans": recent_plans, "outside_plan": outside}
    except Exception:
        return empty
