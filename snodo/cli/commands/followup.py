"""Inspect-command builders for ids surfaced by the CLI.

Every id the CLI prints should be accompanied by the command that inspects it.
These builders are the single source of truth for those suggestions, so a test
can assert each suggested command resolves to a real CLI command.
"""


def session_inspect(session_id: str) -> str:
    """Command to inspect a session."""
    return f"snodo session show {session_id}"


def task_inspect(task_id: str) -> str:
    """Command to inspect a task."""
    return f"snodo task show {task_id}"


def job_inspect(job_id: str) -> str:
    """Command to inspect a job."""
    return f"snodo job status {job_id}"


def recon_inspect(recon_id: str) -> str:
    """Command to inspect a recon."""
    return f"snodo logs {recon_id}"


def task_retry(task_id: str) -> str:
    """Command to retry a failed task."""
    return f'snodo run --retry {task_id} "revised spec"'
