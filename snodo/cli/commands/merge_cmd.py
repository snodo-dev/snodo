"""Merge command — merge a branch into the base branch, gated on CI.

FILE: snodo/cli/commands/merge_cmd.py (Fixes #56)

CI runs on every branch push (``push: branches: ['**']``).  A merge must not
happen on an unverified branch, and an unverified branch must be visibly
different from a green one.  This command is the merge helper: it checks the
branch's latest CI conclusion before merging, and refuses to merge when CI has
not run, is in progress, or has failed.

``--force`` bypasses the gate for a human who has verified the branch by other
means; it is never the default.
"""

import sys
from types import SimpleNamespace

import typer

from snodo.infrastructure.paths import require_project_root
from snodo.infrastructure.ci_gate import (
    CIGateError,
    branch_ci_conclusion,
)
from snodo.infrastructure.worktree import merge_task_branch
from snodo.tools.git import GitError


def register(app: typer.Typer) -> None:
    """Register the top-level merge command (called by discovery loop)."""

    @app.command()
    def merge(
        branch: str = typer.Argument(..., help="Branch to merge into the base branch"),
        force: bool = typer.Option(
            False, "--force", "-f",
            help="Merge even when CI has not run or has failed (human-verified)",
        ),
    ):
        """Merge *branch* into the base branch, gated on the branch's CI conclusion."""
        return merge_command(SimpleNamespace(branch=branch, force=force))


def merge_command(args) -> int:
    """Merge *args.branch* into the base branch, gated on CI.

    Returns 0 on a clean merge, 1 when the merge is refused (CI not green) or
    fails, and 2 on a merge conflict (branch and worktree left intact).
    """
    branch = getattr(args, "branch", "")
    if not branch:
        print("Usage: snodo merge <branch>", file=sys.stderr)
        return 1

    project_root = require_project_root()

    # 1. Check the branch's CI conclusion.  "CI has not run" is a distinct,
    #    visible state — never confused with "CI passed".  --force bypasses
    #    the gate for a human who has verified the branch by other means.
    if not getattr(args, "force", False):
        try:
            conclusion = branch_ci_conclusion(project_root, branch)
        except CIGateError as e:
            print(f"✗ {e}", file=sys.stderr)
            return 1

        if conclusion.state == "pass":
            print(f"✓ CI green on {branch}: {conclusion.detail}")
        elif conclusion.state == "in_progress":
            print(
                f"✗ CI in progress on {branch}: {conclusion.detail}",
                file=sys.stderr,
            )
            print("  Wait for it to finish, then merge again.", file=sys.stderr)
            return 1
        elif conclusion.state == "fail":
            print(
                f"✗ CI failed on {branch}: {conclusion.detail}",
                file=sys.stderr,
            )
            print("  Fix the failure before merging.", file=sys.stderr)
            return 1
        else:  # not_run
            print(
                f"✗ CI has not run on {branch}: {conclusion.detail}",
                file=sys.stderr,
            )
            print(
                "  Push the branch to the remote so CI runs, then merge again. "
                "Use --force only if you have verified the branch by other means.",
                file=sys.stderr,
            )
            return 1
    else:
        print(f"⚠ --force: merging {branch} without a green CI conclusion", file=sys.stderr)

    # 2. Merge.
    try:
        outcome = merge_task_branch(project_root, branch)
    except GitError as e:
        print(f"✗ Merge failed for {branch}: {e}", file=sys.stderr)
        print("  The branch and worktree were left intact for manual resolution.", file=sys.stderr)
        return 1

    if outcome == "merged":
        print(f"✓ Merged {branch} into the base branch")
        return 0

    # Conflict — escalate, leave branch + worktree intact for a human.
    print(f"✗ Merge conflict merging {branch} into the base branch.", file=sys.stderr)
    print("  The branch and worktree were left intact for manual resolution.", file=sys.stderr)
    return 2
