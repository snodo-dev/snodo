"""CI gate — the branch's latest CI conclusion, queried before a merge.

FILE: snodo/infrastructure/ci_gate.py (Fixes #56)

CI runs on every branch push (``push: branches: ['**']``).  A merge must not
happen on an unverified branch, and an unverified branch must be visibly
different from a green one.  This module answers "what is the branch's latest
CI conclusion?" via the GitHub CLI (``gh run list --branch``), with a distinct
``not_run`` state so "CI has not run" is never confused with "CI passed".

A conclusion is only meaningful with its context, so every conclusion carries
the run id, the commit it ran on (``headSha``) and when it concluded.  A run
whose commit is not the branch tip is ``stale`` and is never presented as the
branch's current state (Fixes #76).  ``startup_failure``, ``cancelled`` and
``timed_out`` are distinct from a plain test ``fail``: a run that never started
is not the branch's fault and the operator's next action differs.

The ``gh`` invocation is injectable so tests can stub it without a network
call.
"""

import json
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, List, Optional

# The workflow file that gates merges.  Only runs of this workflow count.
_CI_WORKFLOW = "ci.yml"

# States that mean "a run is imminent but not concluded yet".  Right after a
# push GitHub has not registered the run, so ``not_run`` in that window is a
# race, not a verdict — ``wait_for_ci_conclusion`` polls through these.
_WAIT_STATES = {"not_run", "in_progress", "stale"}


class CIGateError(Exception):
    """Raised when the CI conclusion cannot be determined (operational)."""


@dataclass(frozen=True)
class CIConclusion:
    """The branch's latest CI conclusion.

    ``state`` is one of:
    - ``"pass"`` — the latest run of the CI workflow on this branch succeeded.
    - ``"fail"`` — the latest run failed (conclusion ``failure``).
    - ``"startup_failure"`` — the run never started (broken workflow).  Not
      the branch's fault; the operator should fix the workflow file.
    - ``"cancelled"`` — the run was cancelled (a human or the workflow).
    - ``"timed_out"`` — the run exceeded the workflow timeout.
    - ``"stale"`` — the latest run's commit is not the branch tip; its
      conclusion says nothing about the branch as it stands now.
    - ``"in_progress"`` — a run is queued or in progress; no conclusion yet.
    - ``"not_run"`` — CI has never run on this branch.  Distinct from "pass":
      an unverified branch must be visibly different from a green one.
    """
    state: str
    run_id: Optional[str] = None
    head_sha: Optional[str] = None
    concluded_at: Optional[str] = None
    detail: str = ""


def _run_gh(args: List[str], cwd: str) -> subprocess.CompletedProcess:
    """Run the GitHub CLI, raising CIGateError on failure."""
    try:
        return subprocess.run(
            ["gh", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        raise CIGateError(
            "The GitHub CLI (`gh`) is required to check the branch's CI "
            "conclusion. Install it and authenticate with `gh auth login`."
        )
    except subprocess.TimeoutExpired:
        raise CIGateError("Timed out querying the branch's CI conclusion.")


def branch_ci_conclusion(
    project_root: str,
    branch: str,
    head_sha: Optional[str] = None,
    run_gh: Optional[Callable[[List[str], str], subprocess.CompletedProcess]] = None,
) -> CIConclusion:
    """Return the latest CI conclusion for *branch*.

    Queries ``gh run list --branch <branch> --workflow ci.yml --limit 1
    --json databaseId,status,conclusion,headSha,createdAt,updatedAt``.  The
    latest run (by creation) is authoritative: a branch whose newest run is
    green is green, even if an older run failed.

    *head_sha* is the branch tip the caller is about to merge.  When provided,
    a run whose ``headSha`` differs is reported as ``stale`` instead of as
    that branch's verdict — a conclusion about an old commit says nothing about
    the branch as it stands now (Fixes #76).

    ``run_gh`` is injectable for tests; it defaults to the real ``gh`` CLI.
    """
    run_gh = run_gh or _run_gh
    try:
        proc = run_gh(
            [
                "run", "list",
                "--branch", branch,
                "--workflow", _CI_WORKFLOW,
                "--limit", "1",
                "--json", "databaseId,status,conclusion,headSha,createdAt,updatedAt",
            ],
            project_root,
        )
    except FileNotFoundError:
        raise CIGateError(
            "The GitHub CLI (`gh`) is required to check the branch's CI "
            "conclusion. Install it and authenticate with `gh auth login`."
        )

    if proc.returncode != 0:
        raise CIGateError(
            f"Could not query CI for branch '{branch}': "
            f"{proc.stderr.strip() or proc.stdout.strip()}"
        )

    try:
        runs = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        raise CIGateError(
            f"Unexpected output from `gh run list` for branch '{branch}'."
        )

    if not runs:
        return CIConclusion(
            state="not_run",
            detail=(
                f"CI has never run on branch '{branch}'. Push the branch to "
                "the remote so the CI workflow runs, then merge once it is "
                "green."
            ),
        )

    run = runs[0]
    run_id = str(run.get("databaseId", "")) or None
    status = run.get("status", "")
    conclusion = run.get("conclusion")
    run_sha = run.get("headSha") or None
    concluded_at = run.get("updatedAt") or run.get("createdAt") or None
    sha_note = f" on commit {run_sha[:10]}" if run_sha else ""

    if status in ("queued", "in_progress", "requested", "waiting"):
        return CIConclusion(
            state="in_progress",
            run_id=run_id,
            head_sha=run_sha,
            concluded_at=concluded_at,
            detail=f"CI run {run_id}{sha_note} is {status} on branch '{branch}'.",
        )

    # A completed run whose commit is not the branch tip is stale: its
    # conclusion says nothing about the branch as it stands (Fixes #76).
    if (
        head_sha
        and run_sha
        and run_sha != head_sha
        and conclusion in ("success", "failure", "action_required",
                           "startup_failure", "cancelled", "timed_out")
    ):
        return CIConclusion(
            state="stale",
            run_id=run_id,
            head_sha=run_sha,
            concluded_at=concluded_at,
            detail=(
                f"CI run {run_id} on commit {run_sha[:10]} concluded "
                f"'{conclusion}'{f' at {concluded_at}' if concluded_at else ''}, "
                f"but branch '{branch}' is now at {head_sha[:10]}. That run says "
                "nothing about the branch as it stands; wait for (or trigger) a "
                "run on the current commit."
            ),
        )

    if conclusion == "success":
        return CIConclusion(
            state="pass",
            run_id=run_id,
            head_sha=run_sha,
            concluded_at=concluded_at,
            detail=(
                f"CI run {run_id}{sha_note} passed on branch '{branch}'"
                + (f" at {concluded_at}" if concluded_at else "") + "."
            ),
        )

    if conclusion == "failure":
        return CIConclusion(
            state="fail",
            run_id=run_id,
            head_sha=run_sha,
            concluded_at=concluded_at,
            detail=(
                f"CI run {run_id}{sha_note} concluded 'failure' on branch "
                f"'{branch}'{f' at {concluded_at}' if concluded_at else ''}. "
                "Fix the failure before merging."
            ),
        )

    if conclusion == "startup_failure":
        return CIConclusion(
            state="startup_failure",
            run_id=run_id,
            head_sha=run_sha,
            concluded_at=concluded_at,
            detail=(
                f"CI run {run_id}{sha_note} never started "
                f"(startup_failure). The workflow itself is broken — check "
                ".github/workflows/ci.yml, not the branch."
            ),
        )

    if conclusion == "cancelled":
        return CIConclusion(
            state="cancelled",
            run_id=run_id,
            head_sha=run_sha,
            concluded_at=concluded_at,
            detail=(
                f"CI run {run_id}{sha_note} was cancelled. A human or the "
                "workflow cancelled it; re-run CI rather than fixing a "
                "failure."
            ),
        )

    if conclusion == "timed_out":
        return CIConclusion(
            state="timed_out",
            run_id=run_id,
            head_sha=run_sha,
            concluded_at=concluded_at,
            detail=(
                f"CI run {run_id}{sha_note} timed out. Jobs exceeded the "
                "workflow timeout; re-run CI or lengthen the timeout — not a "
                "branch fix."
            ),
        )

    if conclusion == "action_required":
        return CIConclusion(
            state="fail",
            run_id=run_id,
            head_sha=run_sha,
            concluded_at=concluded_at,
            detail=(
                f"CI run {run_id}{sha_note} requires action on branch "
                f"'{branch}' (action_required)."
            ),
        )

    # status == "completed" with no conclusion, or an unknown conclusion.
    return CIConclusion(
        state="not_run",
        run_id=run_id,
        head_sha=run_sha,
        concluded_at=concluded_at,
        detail=(
            f"CI run {run_id} on branch '{branch}' has no usable conclusion "
            f"(status={status}, conclusion={conclusion}). Treat as not run."
        ),
    )


def wait_for_ci_conclusion(
    project_root: str,
    branch: str,
    head_sha: Optional[str] = None,
    timeout: float = 900.0,
    poll_interval: float = 5.0,
    run_gh: Optional[Callable[[List[str], str], subprocess.CompletedProcess]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
    progress: Optional[Callable[[CIConclusion, float], None]] = None,
) -> CIConclusion:
    """Poll until the branch's CI reaches a conclusion, then return it.

    Right after a push GitHub has not registered the run yet, so an immediate
    query returns ``not_run`` — that window is a race, not a verdict.  This
    polls through the waiting states (``not_run`` / ``in_progress`` / ``stale``)
    until a terminal conclusion or *timeout* elapses, then raises
    :class:`CIGateError` (Fixes #72).

    ``sleep_fn`` and ``progress`` are injectable for tests.  ``progress`` is
    called before each poll sleep with the waiting conclusion and the seconds
    remaining, so an operator sees the merge is waiting, not hung.
    """
    sleep_fn = sleep_fn or time.sleep
    deadline = time.monotonic() + timeout
    last: Optional[CIConclusion] = None

    while True:
        last = branch_ci_conclusion(
            project_root, branch, head_sha=head_sha, run_gh=run_gh,
        )
        if last.state not in _WAIT_STATES:
            return last

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CIGateError(
                f"Timed out after {timeout:.0f}s waiting for CI on branch "
                f"'{branch}' to conclude (last state: {last.state}). "
                f"{last.detail}"
            )

        if progress is not None:
            progress(last, remaining)

        sleep_fn(min(poll_interval, remaining))
