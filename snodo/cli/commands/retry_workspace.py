"""Retry workspace selection and durable starting-point evidence.

A fresh checkout has a separate workspace identity. No directory is moved,
reset, or removed, including directories not registered as project worktrees.
The logical task identity (and therefore plan/retry history) stays unchanged.
"""

import json
import logging
import os
import secrets
from pathlib import Path

from snodo.infrastructure.state import atomic_update_json
from snodo.infrastructure import worktree

_logger = logging.getLogger(__name__)


def setup_attempt(project_root, task_id, spec, *, fresh_start=False, **kwargs):
    """Use the existing lifecycle, with a new workspace identity for fresh runs."""
    task = kwargs.pop("task", None)
    if not fresh_start:
        # A fresh attempt can itself halt. Its next continuation must reuse
        # that checkout, not jump back to the older workspace left beside it.
        state = _read_state(project_root, task_id)
        previous = state.get("worktree_path")
        identity = workspace_identity(task_id, previous)
        if (previous and identity != task_id
                and worktree.worktree_is_owned(project_root, identity, kwargs.get("plan_name"))
                and Path(previous).resolve() == worktree.worktree_path(
                    project_root, identity, kwargs.get("plan_name")
                ).resolve()):
            kwargs["existing_worktree_path"] = previous
        path = worktree.setup_for_task(project_root, task_id, spec, **kwargs)
        if task is not None:
            record_origin(project_root, task, path, kwargs.get("plan_name"), kwargs.get("base"))
        return path
    identity = f"{task_id}_fresh_{secrets.token_hex(6)}"
    kwargs.pop("existing_worktree_path", None)
    print(
        f"  Fresh start: workspace {identity}. Previous attempt {task_id}: "
        "branch, worktree and records left untouched."
    )
    path = worktree.setup_for_task(project_root, identity, spec, **kwargs)
    if task is not None:
        record_origin(project_root, task, path, kwargs.get("plan_name"), kwargs.get("base"))
    return path


def workspace_identity(task_id, path):
    """Return the fresh workspace's lifecycle identity, or the ordinary task id."""
    name = Path(path).name if path else ""
    return name if name.startswith(f"{task_id}_fresh_") else task_id


def record_origin(project_root, task, path, plan_name=None, base=None):
    """Record the actual checkout before dispatch, and tell a continuing coder.

    Git ownership, not directory existence, determines whether work can be
    inherited. An unreadable checkout has unknown revision, not a made-up SHA.
    The root specification stays authoritative; the notice is execution context.
    """
    from snodo.tools.git import open_repo, resolve_base_branch

    identity = workspace_identity(task.id, path)
    origin = {"starting_revision": None, "inherited_work": False, "starting_branch": None,
              "worktree_path": path}
    try:
        if path:
            with open_repo(str(path)) as repo:
                origin["starting_revision"] = repo.head.commit.hexsha
                origin["starting_branch"] = repo.active_branch.name
                if worktree.worktree_is_owned(project_root, identity, plan_name):
                    target = base or resolve_base_branch(project_root)
                    origin["inherited_work"] = bool(
                        repo.git.rev_list(f"{target}..HEAD", max_count=1).strip()
                        or repo.is_dirty(untracked_files=True)
                    )
    except Exception as exc:
        _logger.debug("Could not inspect attempt starting point: %s", exc)

    if os.environ.get("SNODO_BENCHMARK") != "1":
        directory = Path(project_root) / ".snodo" / "tasks" / task.id
        atomic_update_json(directory, "state.json", lambda state: state.update(origin))
        job_id = os.environ.get("SNODO_JOB_ID")
        if job_id and os.environ.get("SNODO_PLAN_JOB") != "1":
            job_dir = Path(project_root) / ".snodo" / "jobs" / job_id
            if job_dir.is_dir():
                atomic_update_json(job_dir, "state.json", lambda state: state.update(origin))

    revision = origin["starting_revision"]
    if revision:
        print(f"  Starting revision: {revision}; inherited work: {origin['inherited_work']}")
    if origin["inherited_work"]:
        task.root_spec = task.root_spec or task.spec
        task.spec += (
            "\n\nContinuation of earlier attempt work: this workspace already "
            f"carries work on {origin['starting_branch']} at {revision}. "
            "The earlier attempt may have halted before its work passed validation. "
            "Inspect and continue that work rather than reproducing it."
        )
    return origin


def _read_state(project_root, task_id):
    try:
        state = json.loads((Path(project_root) / ".snodo" / "tasks" / task_id / "state.json").read_text())
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def read_origin(project_root, task_id):
    """Read starting-point evidence without inferring it from today's branch."""
    state = _read_state(project_root, task_id)
    return {key: state[key] for key in ("starting_revision", "inherited_work", "starting_branch") if key in state}


def print_origin(origin):
    """Render recorded origin facts, including an explicit no-inheritance answer."""
    if origin:
        print(f"  Starting revision: {origin.get('starting_revision') or 'unknown'}")
        print(f"  Inherited work: {origin.get('inherited_work', False)}")
        if origin.get("starting_branch"):
            print(f"  Starting branch: {origin['starting_branch']}")


def attempt_workspace_facts(task, path):
    """Return the branch and lifecycle identity for an attempted workspace."""
    from snodo.infrastructure.worktree import task_worktree_branch

    branch = task_worktree_branch(path) if path else None
    return branch, workspace_identity(task.id, path)


