"""Async job system for background task execution.

FILE: snodo/jobs/__init__.py

Manages .snodo/jobs/<job_id>/ directories with file-based state tracking.
No external dependencies beyond the standard library.
"""

import json
import os
import signal
import time
from pathlib import Path
from typing import List, Optional


class JobError(Exception):
    """Job system error."""


# Valid status transitions: queued -> running -> completed/failed/cancelled
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class JobManager:
    """Manages background jobs in .snodo/jobs/ directories.

    Each job gets a directory: .snodo/jobs/<job_id>/
    containing state.json, task.json, stdout.log, stderr.log.
    """

    def __init__(self, project_root: str):
        """Initialize job manager.

        Args:
            project_root: Path to project root (must contain .snodo/)

        Raises:
            ValueError: If .snodo/ directory doesn't exist
        """
        snodo_dir = Path(project_root) / ".snodo"
        if not snodo_dir.is_dir():
            raise ValueError(f"Not a snodo project: {project_root} (no .snodo/ directory)")
        self.jobs_dir = snodo_dir / "jobs"
        self.jobs_dir.mkdir(exist_ok=True)
        self.project_root = project_root

    def _generate_id(self) -> str:
        """Generate a unique job ID: j_<6-hex> from time.time_ns().

        Retries on collision (extremely unlikely).
        """
        for _ in range(10):
            raw = time.time_ns()
            job_id = f"j_{raw & 0xffffff:06x}"
            if not (self.jobs_dir / job_id).exists():
                return job_id
            time.sleep(0.001)  # Wait 1ms to get a different timestamp
        raise JobError("Failed to generate unique job ID after 10 attempts")

    def _job_dir(self, job_id: str) -> Path:
        """Get the directory for a job, validating the ID."""
        job_path = self.jobs_dir / job_id
        if not job_path.is_dir():
            raise JobError(f"Job not found: {job_id}")
        return job_path

    def _save_state(self, job_dir: Path, state: dict) -> None:
        """Atomically write state.json (write tmp + os.rename)."""
        state_path = job_dir / "state.json"
        tmp_path = job_dir / "state.json.tmp"
        with open(tmp_path, "w") as f:
            json.dump(state, f, indent=2)
        os.rename(str(tmp_path), str(state_path))

    def _load_state(self, job_dir: Path) -> dict:
        """Load state.json from a job directory."""
        state_path = job_dir / "state.json"
        if not state_path.exists():
            raise JobError(f"No state.json in {job_dir.name}")
        with open(state_path) as f:
            return json.load(f)

    def _load_task(self, job_dir: Path) -> dict:
        """Load task.json from a job directory."""
        task_path = job_dir / "task.json"
        if not task_path.exists():
            return {}
        with open(task_path) as f:
            return json.load(f)

    def _reconcile_state(self, job_dir: Path, state: dict) -> dict:
        """Reconcile state with actual process status.

        If status is "running", checks if process is still alive.
        If dead, re-reads state.json (wrapper may have updated it).
        If wrapper crashed without updating, marks as failed.
        """
        if state.get("status") not in ("running", "queued"):
            return state

        pid = state.get("pid")
        if pid is None:
            return state

        try:
            os.kill(pid, 0)  # Check if process is alive
        except ProcessLookupError:
            # Process is dead — re-read state.json (wrapper may have updated it)
            fresh_state = self._load_state(job_dir)
            if fresh_state.get("status") in TERMINAL_STATUSES:
                return fresh_state
            # Wrapper crashed without updating state
            fresh_state["status"] = "failed"
            fresh_state["completed_at"] = time.time()
            fresh_state["exit_code"] = -1
            self._save_state(job_dir, fresh_state)
            return fresh_state
        except PermissionError:
            # Process exists but we can't signal it — still running
            pass

        return state

    def submit(self, task_args: dict) -> str:
        """Submit a new background job.

        Args:
            task_args: Dict with description, protocol, model, mock, verbose,
                      from_pr, cwd (all the args needed to reconstruct the run command)

        Returns:
            Job ID string
        """
        from snodo.jobs.runner import build_command, spawn_background

        job_id = self._generate_id()
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir()

        # Write task.json
        task_path = job_dir / "task.json"
        with open(task_path, "w") as f:
            json.dump(task_args, f, indent=2)

        # Write initial state.json
        state = {
            "status": "queued",
            "pid": None,
            "created_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "exit_code": None,
        }
        self._save_state(job_dir, state)

        # Build command and spawn
        stdout_path = job_dir / "stdout.log"
        stderr_path = job_dir / "stderr.log"

        cmd = build_command(str(job_dir), task_args)
        cwd = task_args.get("cwd", self.project_root)
        pid = spawn_background(cmd, str(stdout_path), str(stderr_path), cwd)

        # Update state with PID
        state["status"] = "running"
        state["pid"] = pid
        state["started_at"] = time.time()
        self._save_state(job_dir, state)

        return job_id

    def list_jobs(self) -> List[dict]:
        """List all jobs, sorted by creation time (newest first).

        Returns:
            List of job summary dicts with id, status, description, created_at.
        """
        jobs: list[dict] = []
        if not self.jobs_dir.exists():
            return jobs

        for entry in self.jobs_dir.iterdir():
            if not entry.is_dir() or not entry.name.startswith("j_"):
                continue
            try:
                state = self._load_state(entry)
                state = self._reconcile_state(entry, state)
                task = self._load_task(entry)
                jobs.append({
                    "id": entry.name,
                    "status": state.get("status", "unknown"),
                    "description": task.get("description", ""),
                    "created_at": state.get("created_at", 0),
                })
            except (JobError, json.JSONDecodeError):
                continue

        jobs.sort(key=lambda j: j["created_at"], reverse=True)
        return jobs

    def get_status(self, job_id: str) -> dict:
        """Get full status for a job, reconciled with process state.

        Args:
            job_id: Job identifier

        Returns:
            Dict with state and task info merged.
        """
        job_dir = self._job_dir(job_id)
        state = self._load_state(job_dir)
        state = self._reconcile_state(job_dir, state)
        task = self._load_task(job_dir)
        return {**state, "id": job_id, "task": task}

    def get_logs(self, job_id: str, stream: str = "stdout", tail: Optional[int] = None) -> str:
        """Read log file for a job — bounded tail read, never the whole file.

        When *tail* is a positive int, only the last N lines are returned.
        The read is O(tail) — it seeks from the end of the file and reads
        only a trailing window (initial 64 KB, expanding up to a 1 MB hard
        cap if the window doesn't yet contain *tail* newlines).

        When *tail* is None or <= 0 the entire file content up to the 1 MB
        hard cap is returned.  An unbounded full-file read is never possible.

        Args:
            job_id: Job identifier
            stream: ``"stdout"`` or ``"stderr"``
            tail:    If set, return only the last *N* lines

        Returns:
            Log content string (empty string when the log file is missing).
        """
        job_dir = self._job_dir(job_id)
        log_file = job_dir / f"{stream}.log"
        if not log_file.exists():
            return ""

        if tail is not None and tail > 0:
            return self._read_tail(log_file, tail)
        else:
            return self._read_capped(log_file)

    @staticmethod
    def _read_tail(log_file: Path, tail: int) -> str:
        """Read the last *tail* lines from *log_file* using a bounded window.

        Never reads the full file.  Starts with a 64 KB window at end of
        file and expands up to a 1 MB hard cap if the window doesn't
        contain *tail* newlines yet.
        """
        _INITIAL_WINDOW = 64 * 1024   # 64 KB
        _MAX_WINDOW = 1024 * 1024     # 1 MB

        with open(log_file, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            file_size = fh.tell()
            if file_size == 0:
                return ""

            window_size = _INITIAL_WINDOW
            content = b""
            while window_size <= _MAX_WINDOW:
                read_start = max(0, file_size - window_size)
                fh.seek(read_start, os.SEEK_SET)
                content = fh.read(window_size)
                newline_count = content.count(b"\n")
                if newline_count >= tail:
                    break
                # Partial first line is acceptable — only expand when we
                # have fewer newlines than requested lines.
                window_size *= 2

        return _decode_tail_lines(content, tail)

    @staticmethod
    def _read_capped(log_file: Path) -> str:
        """Read at most the last 1 MB of the log file."""
        _MAX_CAP = 1024 * 1024  # 1 MB

        with open(log_file, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            file_size = fh.tell()
            if file_size == 0:
                return ""
            read_start = max(0, file_size - _MAX_CAP)
            fh.seek(read_start, os.SEEK_SET)
            content = fh.read(_MAX_CAP)

        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return content.decode("utf-8", errors="replace")

    def wait_for(self, job_id: str, timeout: Optional[float] = None) -> dict:
        """Poll until job reaches terminal state.

        Args:
            job_id: Job identifier
            timeout: Max seconds to wait (None = forever)

        Returns:
            Final job status dict

        Raises:
            JobError: If timeout exceeded
        """
        job_dir = self._job_dir(job_id)
        start = time.time()

        while True:
            state = self._load_state(job_dir)
            state = self._reconcile_state(job_dir, state)
            if state.get("status") in TERMINAL_STATUSES:
                task = self._load_task(job_dir)
                return {**state, "id": job_id, "task": task}

            if timeout is not None and (time.time() - start) >= timeout:
                raise JobError(f"Timeout waiting for job {job_id}")

            time.sleep(1)

    def cancel(self, job_id: str) -> dict:
        """Cancel a running job by sending SIGTERM.

        Args:
            job_id: Job identifier

        Returns:
            Updated state dict

        Raises:
            JobError: If job is already in terminal state
        """
        job_dir = self._job_dir(job_id)
        state = self._load_state(job_dir)

        if state.get("status") in TERMINAL_STATUSES:
            raise JobError(f"Job {job_id} is already {state['status']}")

        pid = state.get("pid")
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # Already dead

        state["status"] = "cancelled"
        state["completed_at"] = time.time()
        self._save_state(job_dir, state)

        return {**state, "id": job_id}

    def _iter_terminal_jobs(self, older_than_days: int) -> list[Path]:
        """Yield job dirs that are terminal and older than *older_than_days*."""
        cutoff = time.time() - (older_than_days * 86400)
        results = []
        if not self.jobs_dir.exists():
            return results
        for entry in sorted(self.jobs_dir.iterdir()):
            if not entry.is_dir() or not entry.name.startswith("j_"):
                continue
            try:
                state = self._load_state(entry)
                state = self._reconcile_state(entry, state)
            except (JobError, json.JSONDecodeError):
                continue
            if state.get("status") not in TERMINAL_STATUSES:
                continue
            created = state.get("created_at", 0)
            if created < cutoff:
                results.append(entry)
        return results

    def archive_jobs(self, older_than_days: int = 10,
                     dry_run: bool = False) -> list[str]:
        """Move terminal jobs older than *older_than_days* to .snodo/jobs_archive/.

        Returns the list of job IDs affected.
        """
        import shutil
        archive_dir = Path(self.project_root) / ".snodo" / "jobs_archive"
        archived = []
        for job_dir in self._iter_terminal_jobs(older_than_days):
            if dry_run:
                archived.append(job_dir.name)
                continue
            archive_dir.mkdir(parents=True, exist_ok=True)
            state = self._load_state(job_dir)
            state["archived_at"] = time.time()
            self._save_state(job_dir, state)
            dest = archive_dir / job_dir.name
            shutil.move(str(job_dir), str(dest))
            archived.append(job_dir.name)
        return archived

    def prune_jobs(self, older_than_days: int = 10,
                   dry_run: bool = False) -> list[str]:
        """Delete terminal jobs older than *older_than_days*.

        Returns the list of job IDs affected.
        """
        import shutil
        pruned = []
        for job_dir in self._iter_terminal_jobs(older_than_days):
            if dry_run:
                pruned.append(job_dir.name)
                continue
            shutil.rmtree(str(job_dir), ignore_errors=True)
            pruned.append(job_dir.name)
        return pruned

    def unarchive_jobs(self, within_days: int = 12,
                       dry_run: bool = False) -> list[str]:
        """Restore archived jobs that were archived within *within_days* days.

        Returns the list of job IDs restored.
        """
        archive_dir = Path(self.project_root) / ".snodo" / "jobs_archive"
        restored = []
        if not archive_dir.exists():
            return restored
        cutoff = time.time() - (within_days * 86400)
        for entry in sorted(archive_dir.iterdir()):
            if not entry.is_dir() or not entry.name.startswith("j_"):
                continue
            try:
                state = JobManager._load_state_static(entry)
            except Exception:
                continue
            archived_at = state.get("archived_at", 0)
            if not isinstance(archived_at, (int, float)) or archived_at < cutoff:
                continue
            if dry_run:
                restored.append(entry.name)
                continue
            dest = self.jobs_dir / entry.name
            dest.mkdir(parents=True, exist_ok=True)
            for child in entry.iterdir():
                child.rename(dest / child.name)
            entry.rmdir()
            restored.append(entry.name)
        return restored

    @staticmethod
    def _load_state_static(job_dir: Path) -> dict:
        """Load state.json without JobManager instance (used for archive dir)."""
        state_path = job_dir / "state.json"
        if not state_path.exists():
            return {}
        with open(state_path) as f:
            return json.load(f)


def _decode_tail_lines(content: bytes, tail: int) -> str:
    """Decode binary content and return the last *tail* lines."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        text = content.decode("utf-8", errors="replace")
    lines = text.splitlines()
    result = "\n".join(lines[-tail:])
    if lines[-tail:]:
        result += "\n"
    return result
