"""Merge command — merge one or more branches into the base branch, gated on CI.

FILE: snodo/cli/commands/merge_cmd.py (Fixes #56, #57, #83)

CI runs on every branch push (``push: branches: ['**']``). A merge must not
happen on an unverified branch, and an unverified branch must be visibly
different from a green one. This command is the merge engine: for each branch
in scope it polls the branch's CI conclusion via ``gh run list`` — waiting,
with visible progress, for a run to appear and conclude — and merges only
branches whose CI is green. Merges are authorised by CI, not by an agent's
self-reported gate results (Fixes #57).

The merge is also the moment the operator looks at an agent's work and decides
its fate, so it is where the review outcome is recorded (Fixes #83). A
measurement that depends on remembering does not get taken; recording the
verdict here makes it part of the merge, not a separate act of discipline:

- ``--review <verdict>`` records the verdict for every merged branch (scripted
  / attended runs).
- otherwise, when stdin is a TTY, the operator is prompted after each merge.
- otherwise (unattended merge — no TTY, no flag), the merge is recorded as
  **unreviewed**, never as accepted. An unreviewed merge must not silently
  count as accepted, or the acceptance rate would measure nothing.

The command operates on the git repository's top level (a git root, not a
``.snodo/`` project), so it can gate and merge in any clone — the repository's
own branch protection is deliberately bypassed by an admin, and a merge must
not depend on a PR existing.

Per branch, in order:
1. skip a branch that does not exist locally;
2. skip a branch already an ancestor of the base branch (no new commits —
   resume-safe after a hand-resolved conflict, which leaves merged branches
   sitting behind the base);
3. poll the branch's CI conclusion (waiting — with visible progress — for a
   run to appear and conclude, since right after a push GitHub has not
   registered it yet): green → merge; not green / stale / not run →
   refuse and STOP, leaving later branches untouched;
4. on a merge conflict, escalate (the branch and any worktree are left intact)
   and STOP.

``--force`` bypasses the CI gate for a human who has verified the branch by
other means; it is never the default.
"""

import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

import typer

from snodo.infrastructure.ci_gate import (
    CIGateError,
    wait_for_ci_conclusion,
)
from snodo.infrastructure.worktree import merge_task_branch
from snodo.tools.git import GitError, resolve_base_branch

from snodo.cli.commands.task_cmd import VALID_VERDICTS

class MergeUsageError(Exception):
    """Raised when the command cannot determine where or what to merge."""


def _resolve_repo_root() -> str:
    """Return the git top-level directory of the current repository.

    Unlike ``require_project_root`` this does not need a ``.snodo/`` project
    marker: the merge engine gates and merges any clone (Fixes #57).
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        raise MergeUsageError("git is required to resolve the repository root.")
    except subprocess.TimeoutExpired:
        raise MergeUsageError("Timed out resolving the repository root.")
    if proc.returncode != 0:
        raise MergeUsageError(
            "Not inside a git repository. Run `snodo merge` from a git clone."
        )
    root = proc.stdout.strip()
    if not root:
        raise MergeUsageError("Could not resolve the repository root.")
    return root


def _short_name(name: str) -> str:
    """Normalise a scope argument to a branch name.

    Accepts ``a``, ``agent-a`` or ``snodo-a`` — all mean the branch
    ``agent-a``. A plain name is prefixed with ``agent-`` only when it looks
    like an agent short name (single letter or digit); anything else is used
    as-is so arbitrary branches can still be merged.
    """
    if name.startswith("agent-"):
        return name
    if name.startswith("snodo-"):
        return "agent-" + name[len("snodo-"):]
    if len(name) == 1 or (len(name) == 2 and name[1].isdigit()):
        return f"agent-{name}"
    return name


def _is_ancestor(repo_root: str, ancestor: str, descendant: str) -> bool:
    """Return True if *ancestor* is an ancestor of *descendant*."""
    try:
        proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", ancestor, descendant],
            cwd=repo_root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _branch_exists(repo: Path, branch: str) -> bool:
    try:
        proc = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return proc.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _branch_head_sha(repo: Path, branch: str) -> Optional[str]:
    """Return the branch tip's commit SHA, or None if it cannot be read.

    The tip is what the merge would land, so the gate compares it against the
    CI run's head commit to detect a stale conclusion (Fixes #76).
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", f"refs/heads/{branch}"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _count_new_commits(repo: Path, base: str, branch: str) -> int:
    try:
        proc = subprocess.run(
            ["git", "rev-list", "--count", f"{base}..{branch}"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip().isdigit():
            return int(proc.stdout.strip())
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return 0


def register(app: typer.Typer) -> None:
    """Register the top-level merge command (called by discovery loop)."""

    @app.command()
    def merge(
        branches: List[str] = typer.Argument(
            ..., help="Branch (or branches) to merge into the base branch",
        ),
        force: bool = typer.Option(
            False, "--force", "-f",
            help="Merge even when CI has not run or has failed (human-verified)",
        ),
        review: Optional[str] = typer.Option(
            None, "--review",
            help="Review verdict for merged work: accepted, amended, or discarded",
        ),
        no_review: bool = typer.Option(
            False, "--no-review",
            help="Never prompt for a review verdict; merged work is recorded as unreviewed",
        ),
    ):
        """Merge branches into the base branch, gated on each branch's CI conclusion."""
        return merge_command(SimpleNamespace(
            branches=branches, force=force,
            review=review, no_review=no_review,
        ))


def merge_command(args) -> int:
    """Merge each branch in ``args.branches`` into the base branch, gated on CI.

    Returns 0 when everything in scope merged, 1 when a merge is refused (CI
    not green), a conflict escalated, or an error occurred, and 2 when the
    command cannot determine where or what to merge.
    """
    raw_branches = getattr(args, "branches", None) or getattr(args, "branch", None)
    if not raw_branches:
        print("Usage: snodo merge <branch> [branch ...]", file=sys.stderr)
        return 2
    if isinstance(raw_branches, str):
        branches = [_short_name(raw_branches)]
    else:
        branches = [_short_name(b) for b in raw_branches]

    try:
        repo = Path(_resolve_repo_root())
    except MergeUsageError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 2

    # Current branch must be the base branch (we do not merge across to
    # another branch). The base is resolved the same way the engine does.
    try:
        base = resolve_base_branch(str(repo))
    except Exception as e:
        print(f"✗ Could not resolve the base branch: {e}", file=sys.stderr)
        return 2

    current = _current_branch(repo)
    if current != base:
        print(
            f"✗ you are on '{current}', not '{base}'. "
            "Merging is only supported on the base branch.",
            file=sys.stderr,
        )
        return 2

    merged_any = False
    for branch in branches:
        if not _branch_exists(repo, branch):
            print(f"— {branch}: no such branch, skipping")
            continue

        n = _count_new_commits(repo, base, branch)
        if n == 0:
            # Resume-safe: after a hand-resolved conflict the branch is already
            # an ancestor of the base, so re-running would re-gate nothing.
            print(f"— {branch}: no new commits, skipping")
            continue

        print(f"▸ merging {branch} ({n} new commit(s))")

        # The CI gate is the whole point: the merge is authorised by the
        # branch's CI conclusion, not by a self-reported gate result (#57).
        if not getattr(args, "force", False):
            # Poll: right after a push GitHub has not registered the run yet,
            # so an immediate "not run" is a race, not a verdict (#72). The
            # gate waits for a run to appear and conclude, with visible
            # progress so the operator can see it is waiting, not hung.
            try:
                head_sha = _branch_head_sha(repo, branch)
                start = time.monotonic()

                def _progress(waiting, remaining):
                    elapsed = int(time.monotonic() - start)
                    print(
                        f"  ⏳ CI on {branch} is {waiting.state} "
                        f"({waiting.detail.split('.')[0]}); "
                        f"waited {elapsed}s, up to {int(remaining)}s left",
                        file=sys.stderr,
                    )

                conclusion = wait_for_ci_conclusion(
                    str(repo),
                    branch,
                    head_sha=head_sha,
                    progress=_progress,
                )
            except CIGateError as e:
                print(f"✗ {e}", file=sys.stderr)
                return 1

            if conclusion.state == "pass":
                print(f"  ✓ CI green on {branch}: {conclusion.detail}")
            else:
                if conclusion.state == "stale":
                    print(f"✗ CI on {branch} is stale: {conclusion.detail}", file=sys.stderr)
                    print("  Wait for a run on the current commit, then merge again.", file=sys.stderr)
                elif conclusion.state == "startup_failure":
                    print(f"✗ CI never started on {branch}: {conclusion.detail}", file=sys.stderr)
                    print("  Fix the CI workflow (.github/workflows/ci.yml), not the branch.", file=sys.stderr)
                elif conclusion.state == "cancelled":
                    print(f"✗ CI was cancelled on {branch}: {conclusion.detail}", file=sys.stderr)
                    print("  Re-run CI, then merge again.", file=sys.stderr)
                elif conclusion.state == "timed_out":
                    print(f"✗ CI timed out on {branch}: {conclusion.detail}", file=sys.stderr)
                    print("  Re-run CI or lengthen the workflow timeout, then merge again.", file=sys.stderr)
                elif conclusion.state == "fail":
                    print(f"✗ CI failed on {branch}: {conclusion.detail}", file=sys.stderr)
                    print("  Fix the failure before merging.", file=sys.stderr)
                elif conclusion.state == "in_progress":
                    print(f"✗ CI in progress on {branch}: {conclusion.detail}", file=sys.stderr)
                    print("  Wait for it to finish, then merge again.", file=sys.stderr)
                else:  # not_run
                    print(f"✗ CI has not run on {branch}: {conclusion.detail}", file=sys.stderr)
                    print(
                        "  Push the branch to the remote so CI runs, then merge again. "
                        "Use --force only if you have verified the branch by other means.",
                        file=sys.stderr,
                    )
                return 1
        else:
            print(f"  ⚠ --force: merging {branch} without a green CI conclusion", file=sys.stderr)

        # Merge.
        try:
            res = merge_task_branch(str(repo), branch)
            if isinstance(res, tuple):
                outcome, conflicting_paths = res
            else:
                outcome, conflicting_paths = res, []
        except GitError as e:
            print(f"✗ Merge failed for {branch}: {e}", file=sys.stderr)
            print("  The branch and worktree were left intact for manual resolution.", file=sys.stderr)
            return 1

        if outcome == "merged":
            print(f"  ✓ Merged {branch} into the base branch")
            merged_any = True
            _record_merge_and_review(args, str(repo), branch)
        else:
            paths_str = ", ".join(conflicting_paths) if conflicting_paths else "unknown path(s)"
            print(f"✗ Merge conflict merging {branch} into the base branch.", file=sys.stderr)
            print(f"  Conflicting path(s): {paths_str}", file=sys.stderr)
            print("  The merge was rolled back (base branch left clean; source branch intact).", file=sys.stderr)
            print(f"  To perform the merge manually and resolve conflicts, run:\n    git merge {branch}", file=sys.stderr)
            return 1

    return 0 if merged_any else 0


def _record_merge_and_review(args, project_root: str, branch: str) -> None:
    """Record the merge and the review verdict in the audit log.

    The merge is the moment the operator looks at the work and decides its
    fate, so that is where the verdict belongs (Fixes #83). Resolution order:

    1. ``--review <verdict>`` — explicit verdict for every merged branch
       (scripted or attended runs).
    2. stdin is a TTY — the operator is prompted after the merge; blank /
       invalid input records **unreviewed** (never silently accepted).
    3. otherwise (unattended merge) — recorded as **unreviewed**.

    ``--no-review`` forces the unattended path even on a TTY (the operator has
    said they are not reviewing here). An unreviewed merge is recorded with an
    explicit ``verdict: unreviewed`` event, so the report counts it as
    unreviewed rather than letting it silently count as accepted.
    """
    try:
        audit_log = _audit_log(project_root)
    except Exception:
        # The merge happened; losing the review record must not fail it, but
        # the unreviewed state is still the honest one and the operator is
        # told. The merge itself is the gate, the review is a measurement.
        print(
            "  ⚠ could not resolve the audit log — review not recorded; "
            "treat this merge as unreviewed",
            file=sys.stderr,
        )
        return

    now = datetime.now(timezone.utc).isoformat()
    audit_log.append_event("task_merged", {
        "op": "task_merged",
        "task_ref": branch,
        "branch": branch,
        "recorded_at": now,
    })

    verdict = None
    review_flag = getattr(args, "review", None)
    no_review = getattr(args, "no_review", False)

    if review_flag:
        v = review_flag.lower()
        if v in VALID_VERDICTS:
            verdict = v
        else:
            print(
                f"  ⚠ invalid --review '{review_flag}' (must be one of "
                f"{', '.join(sorted(VALID_VERDICTS))}) — recording as unreviewed",
                file=sys.stderr,
            )
    elif not no_review and sys.stdin.isatty():
        verdict = _prompt_review(branch)

    verdict = verdict or "unreviewed"
    audit_log.append_event("human_review_recorded", {
        "op": "human_review_recorded",
        "task_ref": branch,
        "branch": branch,
        "verdict": verdict,
        "notes": f"recorded at merge time for {branch}",
        "recorded_at": now,
    })
    if verdict == "unreviewed":
        print(f"  ⚠ no review verdict — recorded {branch} as unreviewed", file=sys.stderr)
    else:
        print(f"  ✓ recorded review verdict '{verdict}' for {branch}")


def _prompt_review(branch: str) -> Optional[str]:
    """Prompt the operator for a review verdict; None means no verdict."""
    print(
        f"  Review merged work on {branch}: "
        f"[a]ccepted / [m]mended / [d]iscarded / [s]kip (unreviewed): ",
        file=sys.stderr,
        end="",
        flush=True,
    )
    try:
        answer = sys.stdin.readline().strip().lower()
    except Exception:
        return None
    mapping = {"a": "accepted", "m": "amended", "d": "discarded", "s": "unreviewed", "": "unreviewed"}
    if answer in mapping:
        v = mapping[answer]
        return None if v == "unreviewed" else v
    print("  (invalid — recording as unreviewed)", file=sys.stderr)
    return None


def _audit_log(project_root: str):
    """Resolve the audit log for the repository, if any.

    ``snodo merge`` operates on a git root that may not be a ``.snodo/``
    project, so the audit log is resolved relative to the project root when
    present and the repository root otherwise. A FRESH ``AuditLog`` is built
    for the resolved path: the process-global ``get_audit_log`` singleton may
    already point at a different repository (or at the repository running the
    suite), and a merge must never append to that (Fixes #65).
    """
    from snodo.infrastructure.audit import AuditLog
    from snodo.infrastructure.paths import resolve_project_root

    project_root = resolve_project_root(project_root) or project_root
    return AuditLog(str(Path(project_root) / ".snodo" / "audit.log"))


def _current_branch(repo: Path) -> Optional[str]:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if proc.returncode == 0:
            return proc.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None
