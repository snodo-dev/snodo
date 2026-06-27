"""Subprocess spawning for background jobs.

FILE: snodo/jobs/runner.py

Builds the command line and spawns the wrapper process.
"""

import subprocess
import sys
from typing import List


def build_command(job_dir: str, task_args: dict) -> List[str]:
    """Construct command to run the wrapper process.

    Args:
        job_dir: Path to the job directory (.snodo/jobs/<job_id>/)
        task_args: Dict with description, protocol, model, mock, verbose, from_pr

    Returns:
        Command list suitable for subprocess.Popen
    """
    cmd = [sys.executable, "-u", "-m", "snodo.jobs.wrapper", job_dir, "run"]

    desc = task_args.get("description", "")
    if desc:
        cmd.append(desc)

    protocol = task_args.get("protocol")
    if protocol:
        cmd.extend(["--protocol", protocol])

    model = task_args.get("model")
    if model:
        cmd.extend(["--model", model])

    if task_args.get("mock"):
        cmd.append("--mock")

    if task_args.get("verbose"):
        cmd.append("--verbose")

    from_pr = task_args.get("from_pr")
    if from_pr is not None:
        cmd.extend(["--from-pr", str(from_pr)])

    return cmd


def spawn_background(cmd: List[str], stdout_path: str, stderr_path: str, cwd: str) -> int:
    """Spawn a background process with output redirected to files.

    Uses start_new_session=True to detach from parent's process group.
    Closes parent's file handles after spawn.

    Args:
        cmd: Command to execute
        stdout_path: Path to stdout log file
        stderr_path: Path to stderr log file
        cwd: Working directory for the child process

    Returns:
        PID of the spawned process
    """
    stdout_f = open(stdout_path, "w")
    stderr_f = open(stderr_path, "w")

    proc = subprocess.Popen(
        cmd,
        stdout=stdout_f,
        stderr=stderr_f,
        cwd=cwd,
        start_new_session=True,
    )

    # Close parent's copies of the file handles
    stdout_f.close()
    stderr_f.close()

    return proc.pid
