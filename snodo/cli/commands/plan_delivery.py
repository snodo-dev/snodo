"""Delivery helpers for plan integration branches."""

import sys

from snodo.cli.commands.run_merge import _deliver_plan_integration
from snodo.core.interfaces import Task
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


def print_blocked_plan_outcome(project_root, plan_name, plan_data, statuses,
                               integration_branch):
    """Print actionable blocked-plan guidance using current Git delivery state."""
    blocked = sorted(
        task_id for task_id, entry in statuses.items()
        if (entry.get("status") if isinstance(entry, dict) else entry) == "blocked"
    )
    if not blocked:
        return
    completed_waves = [
        str(wave.get("id")) for wave in plan_data.get("waves", [])
        if wave.get("tasks") and all(
            (entry.get("status") if isinstance(entry, dict) else entry) == "completed"
            for task_id in wave.get("tasks", [])
            for entry in [statuses.get(task_id)]
        )
    ]
    delivered = False
    if integration_branch:
        try:
            from snodo.tools.git import resolve_base_branch
            with open_repo(str(project_root)) as repo:
                base_ref = resolve_base_branch(str(project_root))
                delivered = repo.git.merge_base(
                    "--is-ancestor", integration_branch, base_ref,
                    with_exceptions=False,
                ) == 0
        except Exception:
            delivered = False
    delivered_waves = ", ".join(completed_waves) if delivered else "none"
    undelivered_waves = "none" if delivered else (", ".join(completed_waves) or "none")
    print(
        f"Plan unfinished: blocked task(s) {', '.join(blocked)}. "
        f"Completed waves delivered to the base branch: {delivered_waves}; "
        f"not delivered: {undelivered_waves}. Fix the blocked task forward "
        "within this plan: replace its spec, then run that wave."
    )


def verify_queue_merge_head(project_root, working_directory, task_ref, protocol, audit_log, branch):
    """Verify one exact plan/queue integration commit before queue delivery."""
    from git import Repo
    from snodo.cli.commands.run_merge import _matching_task_verifications, _quality_validator_ids
    from snodo.validators.context import ValidatorContext
    from snodo.validators.quality import QualityValidator

    with Repo(project_root) as repo:
        target_commit = repo.commit(branch).hexsha
    quality = next(
        (validator for validator in getattr(protocol, "validators", [])
         if validator.validator_type == "quality"),
        None,
    )
    task = Task(id=task_ref, spec=task_ref)
    if quality is None:
        print(
            f"✓ Merged {branch} ungated: task {task_ref} at commit "
            f"{target_commit[:7]} (no quality validator declared).",
            file=sys.stderr,
        )
        return True
    QualityValidator(quality, working_directory=str(working_directory)).evaluate(
        ValidatorContext(
            task=task, protocol=protocol, audit_log=audit_log,
            working_directory=str(working_directory), task_id=task_ref,
        )
    )
    history = audit_log.get_history("verification_executed") if audit_log else []
    passing = [event for event in _matching_task_verifications(
        history, task_ref, target_commit, _quality_validator_ids(protocol),
    ) if event.data.get("outcome") in {"pass", "no_tests"}]
    if passing:
        return True
    reason = (
        f"No passing verification_executed event recorded for task {task_ref} "
        f"at commit {target_commit[:7]}."
    )
    print(f"✗ Refused queue merge for {branch}: {reason}", file=sys.stderr)
    if audit_log:
        audit_log.append_event("unverified_merge_blocked", {
            "op": "unverified_merge_blocked", "task_ref": task_ref,
            "branch": branch, "target_commit": target_commit,
            "reason": reason, "session_id": None,
        })
    return False
