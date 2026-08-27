"""CI gate — the branch's latest CI conclusion, queried before a merge.

FILE: snodo/infrastructure/ci_gate.py (Fixes #56)

CI runs on every branch push (``push: branches: ['**']``).  A merge must not
happen on an unverified branch, and an unverified branch must be visibly
different from a green one.  This module answers "what is the branch's latest
CI conclusion?" via the GitHub CLI (``gh run list --branch``), with a distinct
``not_run`` state so "CI has not run" is never confused with "CI passed".

The ``gh`` invocation is injectable so tests can stub it without a network
call.
"""

import json
import subprocess
from dataclasses import dataclass
from typing import Callable, List, Optional

# The workflow file that gates merges.  Only runs of this workflow count.
_CI_WORKFLOW = "ci.yml"


class CIGateError(Exception):
    """Raised when the CI conclusion cannot be determined (operational)."""


@dataclass(frozen=True)
class CIConclusion:
    """The branch's latest CI conclusion.

    ``state`` is one of:
    - ``"pass"`` — the latest run of the CI workflow on this branch succeeded.
    - ``"fail"`` — the latest run failed (or was cancelled/timed out).
    - ``"in_progress"`` — a run is queued or in progress; no conclusion yet.
    - ``"not_run"`` — CI has never run on this branch.  Distinct from "pass":
      an unverified branch must be visibly different from a green one.
    """
    state: str
    run_id: Optional[str] = None
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
    run_gh: Optional[Callable[[List[str], str], subprocess.CompletedProcess]] = None,
) -> CIConclusion:
    """Return the latest CI conclusion for *branch*.

    Queries ``gh run list --branch <branch> --workflow ci.yml --limit 1
    --json databaseId,status,conclusion``.  The latest run (by creation) is
    authoritative: a branch whose newest run is green is green, even if an
    older run failed.

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
                "--json", "databaseId,status,conclusion",
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

    if status in ("queued", "in_progress", "requested", "waiting"):
        return CIConclusion(
            state="in_progress",
            run_id=run_id,
            detail=f"CI run {run_id} is {status} on branch '{branch}'.",
        )

    if conclusion == "success":
        return CIConclusion(
            state="pass",
            run_id=run_id,
            detail=f"CI run {run_id} passed on branch '{branch}'.",
        )

    if conclusion in ("failure", "cancelled", "timed_out", "action_required"):
        return CIConclusion(
            state="fail",
            run_id=run_id,
            detail=(
                f"CI run {run_id} concluded '{conclusion}' on branch "
                f"'{branch}'. Fix the failure before merging."
            ),
        )

    if conclusion == "startup_failure":
        # The workflow failed before any job ran — typically an invalid
        # workflow definition (bad YAML, malformed step), not the branch's
        # work. Telling the operator to fix the branch sends them to the
        # wrong place (Fixes #74).
        return CIConclusion(
            state="fail",
            run_id=run_id,
            detail=(
                f"CI run {run_id} on branch '{branch}' failed at startup "
                f"(conclusion='{conclusion}') — the workflow itself did not "
                "start, so no job ran. This is a workflow-definition "
                "problem, not a failure of the branch's work. Check "
                ".github/workflows/ (the local suite validates every "
                "workflow file) before re-merging."
            ),
        )

    # status == "completed" with no conclusion, or an unknown conclusion.
    return CIConclusion(
        state="not_run",
        run_id=run_id,
        detail=(
            f"CI run {run_id} on branch '{branch}' has no usable conclusion "
            f"(status={status}, conclusion={conclusion}). Treat as not run."
        ),
    )
