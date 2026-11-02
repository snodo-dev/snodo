"""Keep a plan's integration tree level with the base branch between waves.

A plan's integration branch is cut when the plan starts, so work merged to the
base branch afterwards (a hand merge, another plan) is missing from the tree
later waves build on. These helpers bring the base branch in before each wave
and record the new base commit in the plan's ``status.json``.
"""

import contextlib
from pathlib import Path


def sync_plan_integration(project_root: str, integration_path: Path, integration_branch: str) -> str:
    """Merge the current base branch into the plan integration worktree.

    Returns the integration head after the merge. A conflicting merge is
    aborted and reported, leaving the integration tree as it was.
    """
    from snodo.tools.git import open_repo, resolve_base_branch

    base_ref = resolve_base_branch(project_root)
    with open_repo(str(integration_path)) as repo:
        try:
            repo.git.merge("--no-edit", base_ref)
        except Exception as exc:
            with contextlib.suppress(Exception):
                repo.git.merge("--abort")
            raise RuntimeError(
                f"Cannot update plan integration branch '{integration_branch}' from '{base_ref}': "
                f"merge conflict; resolve the conflict and retry ({exc})"
            ) from exc
        return repo.commit("HEAD").hexsha


def refresh_plan_integration(project_root: str, plan: str, plan_dir, integration_branch) -> None:
    """Sync the integration worktree from the base branch and record its new base sha."""
    if not integration_branch:
        return
    from snodo.infrastructure.state import atomic_update_json
    from snodo.infrastructure.worktree import _name_component, worktree_dir

    integration_path = worktree_dir(project_root) / _name_component(plan) / "integration"
    base_sha = sync_plan_integration(project_root, integration_path, integration_branch)
    atomic_update_json(plan_dir, "status.json", lambda data: data.update(
        integration={"branch": integration_branch, "base_sha": base_sha}
    ), strict=True)
