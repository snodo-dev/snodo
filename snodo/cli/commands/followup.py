"""Inspect-command builders for ids surfaced by the CLI.

Every id the CLI prints should be accompanied by the command that inspects it.
These builders are the single source of truth for those suggestions, so a test
can assert each suggested command resolves to a real CLI command.

A suggestion is only useful if it answers at the moment it is offered. A thing
that has *stopped* is inspected by reading its record; a thing that is *still
running* has no record yet, so it is offered a way to watch it instead. The two
do not resolve the same way: a background job has a live command of its own
(``snodo logs <job_id> --watch``), while a foreground task has no job id and its
only live surface is the dashboard. Use the ``*_followup`` builders to pick the
right suggestion for the state of the thing named.
"""


def session_inspect(session_id: str) -> str:
    """Command to inspect a session."""
    return f"snodo session show {session_id}"


def task_inspect(task_id: str) -> str:
    """Command to read a *finished* task's halt/failure record.

    This answers only once the task has stopped: a running task has no halt or
    failure entry yet. Offer :func:`task_watch` (via :func:`task_followup`)
    while it is alive.
    """
    return f"snodo task show {task_id}"


def task_inspect_json(task_id: str) -> str:
    """Command for a task record's untruncated fields, incl. superseded specs."""
    return f"snodo task show {task_id} --json"


def task_watch() -> str:
    """Live surface for a *running* foreground task.

    A foreground task records no job id, so it cannot be tailed with
    ``snodo logs --watch``; the dashboard is the command that answers about it
    while it is still running.
    """
    return "snodo dashboard"


def task_followup(task_id: str, *, running: bool) -> str:
    """Suggested command for a task, matched to its state.

    A running task is offered the dashboard (its live surface); a finished task
    is offered its halt/failure record.
    """
    return task_watch() if running else task_inspect(task_id)


def job_inspect(job_id: str) -> str:
    """Command to inspect a job (its recorded status)."""
    return f"snodo job status {job_id}"


def job_watch(job_id: str) -> str:
    """Live surface for a *running* job: tail its output until it completes."""
    return f"snodo logs {job_id} --watch"


def job_followup(job_id: str, *, running: bool) -> str:
    """Suggested command for a job, matched to its state.

    A running job is offered a live tail of its output; a finished job is
    offered its recorded status.
    """
    return job_watch(job_id) if running else job_inspect(job_id)


def recon_inspect(recon_id: str) -> str:
    """Command to inspect a recon."""
    return f"snodo logs {recon_id}"


def task_retry(task_id: str) -> str:
    """Command to retry a failed task with its spec unchanged.

    This is the shape the CLI prints: a bare retry keeps what is already
    written, so pasting the suggestion verbatim is always safe. Revising or
    annotating the spec is a deliberate act and lives in the two builders
    below, whose output is only ever offered as an alternative, never as the
    default.
    """
    return f"snodo run --retry {task_id}"


def task_retry_annotate(task_id: str, guidance: str = "note for this attempt") -> str:
    """Command to retry a task with guidance added on top of its existing spec."""
    return f'snodo run --retry {task_id} --append-spec "{guidance}"'


def task_retry_replace(task_id: str, spec: str = "replacement spec") -> str:
    """Command to retry a task with a spec that *replaces* the existing one."""
    return f'snodo run --retry {task_id} --replace-spec "{spec}"'


def task_retry_restore(task_id: str) -> str:
    """How to put a superseded spec back, named without a placeholder value.

    Restoring is itself a replacement, so the text can only come from the
    operator. Printing the option with no argument keeps this line safe to
    paste: the CLI asks for the value instead of overwriting the live spec with
    placeholder words.
    """
    return f"snodo run --retry {task_id} --replace-spec"


def task_retry_options(task_id: str) -> list:
    """Every retry a task at the retry limit can be given, least drastic first.

    Both exhausted-retry sites (``snodo run --retry`` and ``snodo plan run``)
    print this list rather than their own wording, so the shapes a retry can
    take are stated in one place and cannot drift — the drift that made a
    destructive retry the only suggestion an operator ever saw.
    """
    return [
        f"{task_retry(task_id)} (retry with the spec unchanged)",
        f"{task_retry_annotate(task_id)} (keep the spec, add guidance)",
        f"{task_retry_replace(task_id)} (replace the spec)",
        f"snodo task abandon {task_id} (delete branch)",
    ]
