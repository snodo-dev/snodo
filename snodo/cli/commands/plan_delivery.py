"""Delivery helpers for plan integration branches."""

from snodo.cli.commands.run_merge import _deliver_plan_integration
from snodo.infrastructure.worktree import _name_component, worktree_dir
from snodo.tools.git import open_repo


def _remote_branch_matches(repo, remote: str, branch: str, local_sha: str) -> bool:
    """Whether the remote integration ref already names this exact commit."""
    return any(
        len(fields := line.split()) == 2
        and fields[0] == local_sha
        and fields[1] == f"refs/heads/{branch}"
        for line in repo.git.ls_remote(remote, branch).splitlines()
    )


def deliver_healthy_plan_wave(project_root, integration_branch, plan_name,
                              plan_data, protocol, mode, audit_log) -> bool:
    """Deliver the current integration head; return whether delivery failed."""
    delivery_mode = protocol.delivery_for(mode)
    try:
        with open_repo(project_root) as repo:
            if integration_branch not in repo.heads:
                already_delivered = delivery_mode == "local_merge"
            elif delivery_mode == "local_merge":
                already_delivered = repo.git.merge_base(
                    "--is-ancestor", integration_branch, "HEAD", with_exceptions=False
                ) == 0
            elif delivery_mode in {"push_branch", "change_request"}:
                local_sha = repo.commit(integration_branch).hexsha
                remote = getattr(protocol.execution, "delivery_remote", "origin")
                already_delivered = _remote_branch_matches(repo, remote, integration_branch, local_sha)
            else:
                already_delivered = False
    except Exception:
        already_delivered = False
    if already_delivered:
        return False
    result = _deliver_plan_integration(
        str(project_root), integration_branch, str(plan_data.get("name", plan_name)),
        str(plan_data.get("intent", "")), protocol, mode, audit_log,
        integration_path=worktree_dir(str(project_root)) / _name_component(plan_name) / "integration",
        keep_branch=True,
    )
    return bool(result)
