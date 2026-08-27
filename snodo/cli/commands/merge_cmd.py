"""Merge command — merge one or more branches into the base branch, gated on CI.

FILE: snodo/cli/commands/merge_cmd.py (Fixes #56, #57)

CI runs on every branch push (``push: branches: ['**']``). A merge must not
happen on an unverified branch, and an unverified branch must be visibly
different from a green one. This command is the merge engine: for each branch
in scope it checks the branch's latest CI conclusion via ``gh run list``, and
merges only branches whose CI is green. Merges are authorised by CI, not by an
agent's self-reported gate results (Fixes #57).

The command operates on the git repository's top level (a git root, not a
``.snodo/`` project), so it can gate and merge in any clone — the repository's
own branch protection is deliberately bypassed by an admin, and a merge must
not depend on a PR existing.

Per branch, in order:
1. skip a branch that does not exist locally;
2. skip a branch already an ancestor of the base branch (no new commits —
   resume-safe after a hand-resolved conflict, which leaves merged branches
   sitting behind the base);
3. check the branch's CI conclusion: green → merge; not green / not run /
   in progress → refuse and STOP, leaving later branches untouched;
4. on a merge conflict, escalate (the branch and any worktree are left intact)
   and STOP.

``--force`` bypasses the CI gate for a human who has verified the branch by
other means; it is never the default.
"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

import typer

from snodo.infrastructure.ci_gate import (
    CIGateError,
    branch_ci_conclusion,
)
from snodo.infrastructure.worktree import merge_task_branch
from snodo.tools.git import GitError, resolve_base_branch

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
    ):
        """Merge branches into the base branch, gated on each branch's CI conclusion."""
        return merge_command(SimpleNamespace(branches=branches, force=force))


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
            try:
                conclusion = branch_ci_conclusion(str(repo), branch)
            except CIGateError as e:
                print(f"✗ {e}", file=sys.stderr)
                return 1

            if conclusion.state == "pass":
                print(f"  ✓ CI green on {branch}: {conclusion.detail}")
            else:
                if conclusion.state == "in_progress":
                    print(f"✗ CI in progress on {branch}: {conclusion.detail}", file=sys.stderr)
                    print("  Wait for it to finish, then merge again.", file=sys.stderr)
                elif conclusion.state == "fail":
                    print(f"✗ CI failed on {branch}: {conclusion.detail}", file=sys.stderr)
                    print("  Fix the failure before merging.", file=sys.stderr)
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
            outcome = merge_task_branch(str(repo), branch)
        except GitError as e:
            print(f"✗ Merge failed for {branch}: {e}", file=sys.stderr)
            print("  The branch and worktree were left intact for manual resolution.", file=sys.stderr)
            return 1

        if outcome == "merged":
            print(f"  ✓ Merged {branch} into the base branch")
            merged_any = True
        else:
            # Conflict — escalate, leave branch + worktree intact for a human.
            print(f"✗ Merge conflict merging {branch} into the base branch.", file=sys.stderr)
            print("  The branch and worktree were left intact for manual resolution.", file=sys.stderr)
            print("  Resolve it, then re-run `snodo merge` to continue.", file=sys.stderr)
            return 1

    return 0 if merged_any else 0


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
