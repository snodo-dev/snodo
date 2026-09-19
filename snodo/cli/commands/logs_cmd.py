"""Unified logs command — handles both job IDs and recon IDs.

FILE: snodo/cli/commands/logs_cmd.py
"""

import logging
import sys
import time
from pathlib import Path
from types import SimpleNamespace
import typer

_logger = logging.getLogger(__name__)


def _watch_renderer():
    """Return a styled progress renderer for a live watch, or None.

    None on a non-tty stdout, a redirected stream, or under ``NO_COLOR``: the
    watch then takes the exact ``print(line, end="")`` path it always has, so
    its output there is byte-identical to before (#294). On an interactive
    terminal the renderer colours the engine's line kinds and compacts repeated
    tool turns in place. Presentation only — the job's own stdout.log is
    untouched, and a line still appears in the same order.
    """
    from snodo.engine.progress import ProgressRenderer, color_enabled

    if not color_enabled():
        return None
    return ProgressRenderer()


def _emit_watch_line(renderer, line: str) -> None:
    """Write one log line to the console, styled when a renderer is present.

    A line without its terminating newline is a partial tail (the writer has
    not finished the line): it is passed through verbatim with no added
    newline, exactly as the watch path always printed it, and the renderer's
    in-place turn state is reset so a following turn cannot be drawn over it.
    """
    if renderer is None or not line.endswith("\n"):
        if renderer is not None:
            renderer.reset()
        print(line, end="", flush=True)
        return
    renderer(line.rstrip("\n"))


def register(app: typer.Typer) -> None:
    """Register top-level CLI commands onto app (called by discovery loop)."""

    @app.command()
    def logs(
        composite_id: str = typer.Argument(..., help="Job ID (j_xxx) or Recon ID (rec_xxx)"),
        watch: bool = typer.Option(False, "--watch", "-w", help="Tail job logs in real time until job completes"),
    ):
        """Show output for a job or recon by ID."""
        args = SimpleNamespace(composite_id=composite_id, watch=watch)
        return logs_command(args)



def logs_command(args) -> int:
    """Show output for a job or recon by ID."""
    from snodo.infrastructure.paths import require_project_root

    composite_id = getattr(args, "composite_id", "")
    if not composite_id:
        print("Error: <id> is required", file=sys.stderr)
        return 1

    project_root = require_project_root()

    if composite_id.startswith("rec_"):
        return _show_recon(project_root, composite_id)
    if composite_id.startswith("j_"):
        return _show_job(project_root, composite_id, args)
    if composite_id.startswith("task_"):
        return _show_task_logs(project_root, composite_id, args)

    # Try job first, then recon, then task
    if _job_exists(project_root, composite_id):
        return _show_job(project_root, composite_id, args)
    if _recon_exists(project_root, composite_id):
        return _show_recon(project_root, composite_id)
    if (Path(project_root) / ".snodo" / "tasks" / composite_id).is_dir():
        return _show_task_logs(project_root, composite_id, args)
    if (Path(project_root) / ".snodo" / "plans" / composite_id).is_dir():
        return _show_plan_name_logs(project_root, composite_id, args)
    if _has_task_jobs(project_root, composite_id):
        return _show_task_logs(project_root, composite_id, args)

    print(f"Error: {composite_id!r} not found as a job or recon ID.",
          file=sys.stderr)
    return 1


def _has_task_jobs(project_root: str, task_id: str) -> bool:
    """Check if any background jobs exist for the given task_id."""
    from pathlib import Path
    import json

    jobs_dir = Path(project_root) / ".snodo" / "jobs"
    if jobs_dir.is_dir():
        for entry in jobs_dir.iterdir():
            if not entry.is_dir() or not entry.name.startswith("j_"):
                continue
            task_json = entry / "task.json"
            if not task_json.exists():
                continue
            try:
                td = json.loads(task_json.read_text())
                if td.get("task_id") == task_id or td.get("description", "").startswith(task_id):
                    return True
            except Exception:
                continue
    return False


def _show_task_logs(project_root: str, task_id: str, args) -> int:
    """Show output for a task. If background jobs exist, show job logs; otherwise guide user."""
    from pathlib import Path
    import json

    jobs_dir = Path(project_root) / ".snodo" / "jobs"
    matching_jobs = []
    if jobs_dir.is_dir():
        for entry in sorted(jobs_dir.iterdir()):
            if not entry.is_dir() or not entry.name.startswith("j_"):
                continue
            task_json = entry / "task.json"
            if not task_json.exists():
                continue
            try:
                td = json.loads(task_json.read_text())
                if td.get("task_id") == task_id or td.get("description", "").startswith(task_id):
                    matching_jobs.append(entry.name)
            except Exception:
                continue

    if matching_jobs:
        latest_job = matching_jobs[-1]
        return _show_job(project_root, latest_job, args)

    task_dir = Path(project_root) / ".snodo" / "tasks" / task_id
    if task_dir.is_dir():
        print(f"Task {task_id} was executed in the foreground (console output streamed directly during execution).", file=sys.stderr)
        print(f"  Use 'snodo meta {task_id}' to inspect turn telemetry, token usage, and costs.", file=sys.stderr)
        print(f"  Use 'snodo task show {task_id}' to inspect failure/halt records.", file=sys.stderr)
        return 0

    print(f"Error: {task_id!r} not found as a task, job, or recon ID.", file=sys.stderr)
    return 1


def _job_exists(project_root: str, job_id: str) -> bool:
    from snodo.jobs import JobManager
    try:
        mgr = JobManager(project_root)
        mgr._job_dir(job_id)
        return True
    except Exception:
        return False


def _recon_exists(project_root: str, recon_id: str) -> bool:
    from pathlib import Path
    recon_dir = Path(project_root) / ".snodo" / "recons" / recon_id
    return recon_dir.is_dir()


def _show_plan_name_logs(project_root: str, plan_name: str, args) -> int:
    """Show logs for a plan by its plan name, finding its latest plan-run job."""
    from snodo.jobs import JobManager

    mgr = JobManager(project_root)
    matching = [j for j in mgr.list_jobs() if j.get("plan") == plan_name]
    if matching:
        latest_job_id = matching[0]["id"]
        return _show_job(project_root, latest_job_id, args)

    from snodo.cli.commands.plan_cmd import _plan_status
    from snodo.mcp.planner import PlannerMCP
    try:
        planner = PlannerMCP(project_root)
        print(f"No active background job found for plan '{plan_name}'. Current plan status:\n")
        return _plan_status(planner, plan_name)
    except Exception as e:
        print(f"Plan '{plan_name}' found, but could not read status: {e}", file=sys.stderr)
        return 1


def _show_plan_job(project_root: str, job_id: str, args) -> int:
    """Show progress for a plan-run job, following its child task jobs."""
    from snodo.jobs import JobManager, TERMINAL_STATUSES
    from snodo.engine.progress import format_duration
    import json

    mgr = JobManager(project_root)
    watch = getattr(args, "watch", False)

    try:
        job_status = mgr.get_status(job_id)
    except Exception as e:
        print(f"Error: Could not get status for job {job_id}: {e}", file=sys.stderr)
        return 1

    task_data = job_status.get("task", {}) if isinstance(job_status.get("task"), dict) else {}
    plan_name = task_data.get("plan_name") or job_status.get("plan") or ""

    planner = None
    try:
        from snodo.mcp.planner import PlannerMCP
        planner = PlannerMCP(project_root)
    except Exception as e:
        _logger.debug("PlannerMCP unavailable: %s", e)

    plan_data = {}
    if planner and plan_name:
        try:
            plan_obj = planner.get_plan(plan_name)
            plan_data = plan_obj.to_dict() if hasattr(plan_obj, "to_dict") else plan_obj
        except Exception as e:
            _logger.debug("Could not get plan from planner: %s", e)

    if not plan_data and plan_name:
        plan_file = Path(project_root) / ".snodo" / "plans" / plan_name / "plan.yml"
        if plan_file.exists():
            import yaml
            try:
                plan_data = yaml.safe_load(plan_file.read_text()) or {}
            except Exception as e:
                _logger.debug("Could not read plan.yml: %s", e)

    intent = plan_data.get("intent", "") if isinstance(plan_data, dict) else ""
    waves = plan_data.get("waves", []) if isinstance(plan_data, dict) else []

    def _get_plan_tasks() -> dict:
        if planner and plan_name:
            try:
                sd = planner.get_status(plan_name)
                return sd.get("tasks", {})
            except Exception as e:
                _logger.debug("Could not get plan tasks from planner: %s", e)
        status_file = Path(project_root) / ".snodo" / "plans" / plan_name / "status.json"
        if status_file.exists():
            try:
                sd = json.loads(status_file.read_text())
                return sd.get("tasks", {})
            except Exception as e:
                _logger.debug("Could not read status.json: %s", e)
        return {}

    def _fetch_child_jobs() -> list[dict]:
        if hasattr(mgr, "get_child_jobs"):
            try:
                return mgr.get_child_jobs(job_id)
            except Exception as e:
                _logger.debug("get_child_jobs failed: %s", e)
        try:
            children = [j for j in mgr.list_jobs() if j.get("parent_job") == job_id]
            children.sort(key=lambda j: j.get("created_at", 0))
            return children
        except Exception as e:
            _logger.debug("list_jobs for children failed: %s", e)
            return []

    def _print_status_view(tasks_status: dict, child_jobs: list[dict]) -> None:
        _STATUS_MARKERS = {
            "completed": "+", "in_progress": "~", "running": "~",
            "blocked": "!", "errored": "?", "unmerged": "u", "pending": " ",
        }
        task_children: dict[str, dict] = {}
        for cj in child_jobs:
            t_ref = cj.get("task_ref")
            if t_ref:
                task_children[t_ref] = cj

        if waves:
            for wave in waves:
                wave_id = wave.get("id") if isinstance(wave, dict) else getattr(wave, "id", "")
                deps = wave.get("depends_on", []) if isinstance(wave, dict) else getattr(wave, "depends_on", [])
                dep_str = f" (depends on: {', '.join(str(d) for d in deps)})" if deps else ""
                print(f"  Wave {wave_id}{dep_str}:")
                task_list = wave.get("tasks", []) if isinstance(wave, dict) else getattr(wave, "tasks", [])
                for task_id in task_list:
                    raw = tasks_status.get(task_id, "pending")
                    state = raw["status"] if isinstance(raw, dict) else raw
                    marker = _STATUS_MARKERS.get(state, "?")
                    cj = task_children.get(task_id)
                    if cj:
                        cid = cj["id"]
                        dur = cj.get("duration_seconds")
                        dur_str = f" in {format_duration(dur)}" if dur is not None else ""
                        print(f"    [{marker}] {task_id}: {state} (job {cid}){dur_str}")
                    else:
                        print(f"    [{marker}] {task_id}: {state}")
            print()
        elif child_jobs:
            for cj in child_jobs:
                cid = cj["id"]
                t_ref = cj.get("task_ref") or "task"
                c_stat = cj.get("status", "")
                dur = cj.get("duration_seconds")
                dur_str = f" in {format_duration(dur)}" if dur is not None else ""
                print(f"  [{t_ref}] {c_stat} (job {cid}){dur_str}")
            print()

        total = len(tasks_status) if tasks_status else len(child_jobs)
        if total:
            if tasks_status:
                done = sum(1 for s in tasks_status.values() if (s["status"] if isinstance(s, dict) else s) == "completed")
                blocked = sum(1 for s in tasks_status.values() if (s["status"] if isinstance(s, dict) else s) == "blocked")
                errored = sum(1 for s in tasks_status.values() if (s["status"] if isinstance(s, dict) else s) == "errored")
                unmerged = sum(1 for s in tasks_status.values() if (s["status"] if isinstance(s, dict) else s) == "unmerged")
            else:
                done = sum(1 for c in child_jobs if c.get("status") == "completed")
                blocked = sum(1 for c in child_jobs if c.get("status") == "blocked")
                errored = sum(1 for c in child_jobs if c.get("status") in ("failed", "errored"))
                unmerged = sum(1 for c in child_jobs if c.get("status") == "unmerged")
            print(f"Progress: {done}/{total} completed", end="")
            if unmerged:
                print(f", {unmerged} unmerged", end="")
            if blocked:
                print(f", {blocked} blocked", end="")
            if errored:
                print(f", {errored} errored", end="")
            print()

    cur_status = job_status.get("status", "unknown")
    child_jobs = _fetch_child_jobs()
    tasks_status = _get_plan_tasks()

    # Establish which shape this plan run has rather than assuming it. A plan
    # that ran its tasks in-process wrote the whole run — validator turns, tool
    # calls, coder narration — to its own stdout.log, and the reader who asked
    # to follow the run wants that output, not only the summary they already
    # saw from `plan status`. A plan that spawned a child task job per task
    # leaves the detail in those children, and the summary plus child ids is
    # the answer (#272, #279). The two are told apart by whether the run owns
    # child jobs; the summary is printed either way, as framing.
    if not watch:
        print(f"Job {job_id} is a plan run (plan: {plan_name or 'unnamed'}, status: {cur_status}).")
        if intent:
            print(f"Intent: {intent}")
        if child_jobs:
            print("Output is produced by child task jobs.\n")
            _print_status_view(tasks_status, child_jobs)
            _print_child_log_hints(child_jobs)
        else:
            # No children: the run's output is the plan job's own stdout.
            _print_status_view(tasks_status, child_jobs)
            _stream_job_stdout(project_root, job_id, args)
        print(f"  Follow this plan live: snodo logs {job_id} --watch")
        return job_status.get("exit_code", 0) or 0

    print(f"Following plan run {job_id} (plan: {plan_name or 'unnamed'})")
    if intent:
        print(f"Intent: {intent}")
    print()

    if cur_status in TERMINAL_STATUSES:
        _print_status_view(tasks_status, child_jobs)
        if not child_jobs:
            # In-process run: its stdout.log is the run, even once ended.
            _stream_job_stdout(project_root, job_id, args)
        print(f"Plan run {job_id} finished ({cur_status}).", flush=True)
        return job_status.get("exit_code", 0) or 0

    _print_status_view(tasks_status, child_jobs)

    seen_child_status: dict[str, str] = {cj["id"]: cj.get("status", "") for cj in child_jobs}
    seen_task_status: dict[str, str] = {
        tid: (entry["status"] if isinstance(entry, dict) else entry)
        for tid, entry in tasks_status.items()
    }

    # The plan's own stdout is followed alongside its children: an in-process
    # run puts everything there, a child-spawning run leaves it effectively
    # empty, so reading it is free and neither shape is guessed at from one
    # snapshot — a plan that has not yet spawned its first child still shows
    # its own output the moment it appears.
    try:
        log_path = mgr._job_dir(job_id) / "stdout.log"
    except Exception:
        log_path = Path(project_root) / ".snodo" / "jobs" / job_id / "stdout.log"
    try:
        own_log = open(log_path) if log_path.exists() else None
    except OSError:
        own_log = None

    def _drain_own() -> None:
        if own_log is None:
            return
        try:
            while True:
                line = own_log.readline()
                if not line:
                    break
                _emit_watch_line(watch_renderer, line)
        except (OSError, ValueError):
            pass

    watch_renderer = _watch_renderer()

    # Liveness (#316, #321): the loop polls every second but prints only when a
    # child's status CHANGES, so a wave that runs healthy can leave the
    # terminal motionless for twenty minutes — a quiet run is
    # indistinguishable from a dead process, and killing a frozen screen is
    # the wrong remedy for a healthy one. On an interactive stream only, one
    # line at the foot of the output keeps the elapsed time of what is running
    # current. It goes through the renderer as a heartbeat line, so a run of
    # heartbeats shares one row that is repainted with the newest numbers
    # rather than committing a row per poll: an hour of quiet watching costs
    # one row, and a real turn, transition or halt arriving below ends the run
    # so the next heartbeat starts a fresh row under it. The poll itself is
    # unchanged, and a non-tty, redirected or NO_COLOR watch (renderer None)
    # never draws or appends a heartbeat, so its append-only lines stay
    # exactly what they are today — nothing to flood a log or a pipe. This is
    # presentation only: it adds no state and no status, changes what is
    # emitted by a transition in no way, and touches no log or audit record.
    _HEARTBEAT_POLLS = 5
    polls_since_heartbeat: dict = {"n": 0}

    def _emit(text: str) -> None:
        """One complete console line: through the window when it is live.

        A transition line goes through the renderer when one exists so the
        painted block and the transition never fight for the cursor, and a
        direct print otherwise — byte-for-byte what the watch printed before.
        """
        _emit_watch_line(watch_renderer, text + "\n")

    def _draw_heartbeat(children: list[dict], plan_state: dict) -> None:
        if watch_renderer is None:
            return
        parts = ["  ~ watching"]
        running = [cj for cj in children if cj.get("status") not in TERMINAL_STATUSES]
        started = plan_state.get("started_at") if isinstance(plan_state, dict) else None
        if started:
            parts.append(f"plan {format_duration(time.time() - started)}")
        if running:
            oldest = min(running, key=lambda c: c.get("created_at") or 0)
            ref = oldest.get("task_ref") or oldest.get("id") or "?"
            seg = f"{len(running)} running: {ref}"
            dur = oldest.get("duration_seconds")
            if dur is not None:
                seg += f" {format_duration(dur)}"
            parts.append(seg)
        parts.append("no status change")
        # The renderer owns the row: the first heartbeat claims it, each later
        # one of the same run repaints it in place with the newer numbers.
        watch_renderer(" · ".join(parts))

    try:
        while True:
            _drain_own()
            time.sleep(1.0)
            try:
                current_job = mgr.get_status(job_id)
            except Exception as e:
                _logger.debug("Error checking plan job %s: %s", job_id, e)
                continue

            current_plan_status = current_job.get("status", "unknown")
            children = _fetch_child_jobs()
            status_changed = False

            for cj in children:
                cid = cj["id"]
                cstat = cj.get("status", "")
                t_ref = cj.get("task_ref") or cid
                prev = seen_child_status.get(cid)
                if prev is None:
                    seen_child_status[cid] = cstat
                    status_changed = True
                    dur = cj.get("duration_seconds")
                    dur_str = f" in {format_duration(dur)}" if dur is not None and cstat in TERMINAL_STATUSES else ""
                    _emit(f"  [{t_ref}] {cstat} (job {cid}){dur_str}")
                elif prev != cstat:
                    seen_child_status[cid] = cstat
                    status_changed = True
                    dur = cj.get("duration_seconds")
                    dur_str = f" in {format_duration(dur)}" if dur is not None and cstat in TERMINAL_STATUSES else ""
                    _emit(f"  [{t_ref}] {cstat} (job {cid}){dur_str}")

            cur_tasks_status = _get_plan_tasks()
            for tid, raw in cur_tasks_status.items():
                tstat = raw["status"] if isinstance(raw, dict) else raw
                prev_tstat = seen_task_status.get(tid)
                if prev_tstat != tstat:
                    seen_task_status[tid] = tstat
                    status_changed = True
                    if tstat in ("blocked", "errored", "unmerged") and not any(cj.get("task_ref") == tid for cj in children):
                        _emit(f"  [{tid}] {tstat}")

            if status_changed and watch_renderer is not None and current_plan_status not in TERMINAL_STATUSES:
                # The opening tree is a snapshot. End the heartbeat window and
                # append a fresh one after a real status change so it cannot be
                # mistaken for the current plan state. Redirected watches keep
                # their historical append-only output unchanged.
                watch_renderer.reset()
                print()
                _print_status_view(cur_tasks_status, children)

            if current_plan_status in TERMINAL_STATUSES:
                _drain_own()
                # The window is over: break it before the direct prints below
                # so no repaint can climb up into the final status view.
                if watch_renderer is not None:
                    watch_renderer.reset()
                print()
                _print_status_view(cur_tasks_status, children)
                print(f"Plan run {job_id} finished ({current_plan_status}).", flush=True)
                return current_job.get("exit_code", 0) or 0

            polls_since_heartbeat["n"] += 1
            if polls_since_heartbeat["n"] >= _HEARTBEAT_POLLS:
                polls_since_heartbeat["n"] = 0
                _draw_heartbeat(children, current_job)
    except KeyboardInterrupt:
        pass
    finally:
        if own_log is not None:
            own_log.close()
    return 0


def _print_child_log_hints(child_jobs: list[dict]) -> None:
    """Name the real command that reaches a child task's logs.

    A generic "use snodo logs <child_job_id>" leaves the reader to find the id
    themselves. Every child's record carries its id, so print the command with
    it. Hints stay off healthy children: only a child that needs a look
    (blocked, failed, errored, unmerged, running) earns a line, so a wave with
    one task and a wave with six both stay readable.
    """
    interesting = ("blocked", "failed", "errored", "unmerged", "running")
    hinted = [
        cj for cj in child_jobs
        if cj.get("id") and cj.get("status") in interesting
    ]
    if not hinted:
        return
    print()
    for cj in hinted:
        ref = cj.get("task_ref") or "task"
        print(f"  logs for {ref} ({cj.get('status')}): snodo logs {cj['id']}")


def _show_job(project_root: str, job_id: str, args) -> int:
    """Show job stdout, delegating to the existing job handler."""
    from snodo.jobs import JobManager
    import json

    mgr = JobManager(project_root)

    try:
        job_dir = mgr._job_dir(job_id)
    except Exception:
        job_dir = Path(project_root) / ".snodo" / "jobs" / job_id

    task_file = job_dir / "task.json"
    state_file = job_dir / "state.json"
    is_plan_job = False

    if task_file.exists():
        try:
            task_data = json.loads(task_file.read_text())
            if task_data.get("plan_name"):
                is_plan_job = True
        except Exception as e:
            _logger.debug("Could not read task.json: %s", e)

    if state_file.exists() and not is_plan_job:
        try:
            state_data = json.loads(state_file.read_text())
            if state_data.get("job_type") == "plan":
                is_plan_job = True
        except Exception as e:
            _logger.debug("Could not read state.json: %s", e)

    if not is_plan_job and not (job_dir / "stdout.log").exists():
        try:
            st = mgr.get_status(job_id)
            t = st.get("task", {}) if isinstance(st.get("task"), dict) else {}
            if st.get("job_type") == "plan" or t.get("plan_name") or st.get("plan"):
                is_plan_job = True
        except Exception as e:
            _logger.debug("Could not get status for job %s: %s", job_id, e)

    if is_plan_job:
        return _show_plan_job(project_root, job_id, args)

    return _stream_job_stdout(project_root, job_id, args)


def _stream_job_stdout(project_root: str, job_id: str, args) -> int:
    """Stream a job's own stdout.log — the run's output — to the console.

    One code path serves a plain task job and a plan run that executed its
    tasks in-process: ``--watch`` tails the file until the job reaches a
    terminal status and then drains what remains, while a plain read prints
    the log once. The plan branch must reach the same output the same way
    rather than re-implementing it, so the two can never drift on what
    "following the output" means.
    """
    from snodo.jobs import JobManager, TERMINAL_STATUSES

    mgr = JobManager(project_root)
    watch = getattr(args, "watch", False)

    try:
        job_dir = mgr._job_dir(job_id)
    except Exception:
        job_dir = Path(project_root) / ".snodo" / "jobs" / job_id
    log_path = job_dir / "stdout.log"

    if not watch:
        content = mgr.get_logs(job_id)
        if content:
            print(content, end="")
        else:
            print("(no stdout output)")
        return 0

    # Check if job is already finished and produced no stdout output
    has_content = log_path.exists() and log_path.stat().st_size > 0
    if not has_content:
        try:
            st = mgr.get_status(job_id)
            if st.get("status") in TERMINAL_STATUSES:
                print("(no stdout output)")
                return 0
        except Exception as e:
            _logger.debug("Could not get terminal status for job %s: %s", job_id, e)

    if not log_path.exists():
        # Wait briefly for stdout.log to appear if job is running
        waited = 0
        while not log_path.exists():
            try:
                status = mgr.get_status(job_id)
                if status.get("status") in TERMINAL_STATUSES:
                    print("(no stdout output)")
                    return 0
            except Exception as e:
                _logger.debug("Could not get status while waiting for stdout.log: %s", e)
            time.sleep(0.2)
            waited += 1
            if waited > 25:  # 5 seconds
                print("(no stdout output — file not created yet)")
                return 1

    lines_printed = 0
    watch_renderer = _watch_renderer()
    try:
        with open(log_path) as f:
            f.seek(0)
            while True:
                line = f.readline()
                if line:
                    _emit_watch_line(watch_renderer, line)
                    lines_printed += 1
                else:
                    try:
                        status = mgr.get_status(job_id)
                        if status.get("status") in TERMINAL_STATUSES:
                            while True:
                                line = f.readline()
                                if line:
                                    _emit_watch_line(watch_renderer, line)
                                    lines_printed += 1
                                else:
                                    break
                            break
                    except Exception as e:
                        _logger.debug("Could not read job status while following logs: %s", e)
                    time.sleep(0.5)
        if lines_printed == 0:
            print("(no stdout output)")
    except KeyboardInterrupt:
        pass
    return 0


def _show_recon(project_root: str, recon_id: str) -> int:
    """Show recon results from results.json."""
    from pathlib import Path
    import json

    recon_dir = Path(project_root) / ".snodo" / "recons" / recon_id
    if not recon_dir.is_dir():
        print(f"Error: Recon not found: {recon_id}", file=sys.stderr)
        return 1

    state_path = recon_dir / "state.json"
    results_path = recon_dir / "results.json"

    state = {}
    if state_path.exists():
        try:
            with open(state_path) as f:
                state = json.load(f)
        except Exception as e:
            _logger.warning("Could not read recon state %s: %s", state_path, e)

    results = []
    if results_path.exists():
        try:
            with open(results_path) as f:
                results = json.load(f)
        except Exception as e:
            _logger.warning("Could not read recon results %s: %s", results_path, e)

    query = state.get("query", "—")
    status = state.get("status", "—")
    agents = state.get("agents", [])

    print(f"Recon: {recon_id}")
    print(f"Query: {query}")
    print(f"Status: {status}")
    if agents:
        from snodo.recon import normalize_recon_agents
        lanes = normalize_recon_agents(agents)
        # A lane is a failover chain; name its head, and show the order when
        # more than one model can be reached.
        print(f"Agents: {', '.join(lane[0] for lane in lanes)}")
        for lane in lanes:
            if len(lane) > 1:
                print(f"  Failover order for {lane[0]}: {' → '.join(lane)}")
    print()

    for r in results:
        agent = r.get("agent", "—")
        model = r.get("model", "—")
        result_text = r.get("result", "").strip()
        error = r.get("error", "")
        attempts = r.get("attempts", [])

        print(f"--- {agent} ({model}) ---")
        for attempt in attempts:
            attempt_model = attempt.get("model", "—")
            attempt_error = attempt.get("error", None)
            if attempt_error:
                print(f"  tried {attempt_model}: {attempt_error}")
        if error:
            print(f"Error: {error}")
        if result_text:
            print(result_text)
        else:
            print("(empty result)")
        print()

    if not results:
        print("No results yet.")

    return 0
