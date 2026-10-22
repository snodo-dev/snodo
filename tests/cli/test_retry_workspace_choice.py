"""Additive coverage for retry origin and clean-start selection."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from git import Repo

from snodo.cli.commands.retry_workspace import (
    setup_attempt, record_origin, read_origin, print_origin, workspace_identity,
)
from snodo.core.interfaces import Task
from snodo.infrastructure import worktree


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    with Repo.init(root, initial_branch="main") as repo:
        with repo.config_writer() as config:
            config.set_value("user", "name", "Test")
            config.set_value("user", "email", "test@example.org")
        (root / "base.txt").write_text("base")
        repo.index.add(["base.txt"])
        repo.index.commit("base")
    monkeypatch.setattr(worktree, "_prepare_task_environment", lambda *a: None)
    monkeypatch.delenv("SNODO_JOB_ID", raising=False)
    monkeypatch.delenv("SNODO_BENCHMARK", raising=False)
    return str(root)


def test_continue_reuses_branch_and_records_coder_notice(project):
    path = setup_attempt(project, "task_abc", "implement feature")
    with Repo(path) as repo:
        (Path(path) / "feature.txt").write_text("earlier attempt")
        repo.index.add(["feature.txt"])
        prior = repo.index.commit("halted work").hexsha
        branch = repo.active_branch.name
    assert setup_attempt(project, "task_abc", "implement feature") == path
    task = Task(id="task_abc", spec="implement feature")
    origin = record_origin(project, task, path)
    assert origin["starting_revision"] == prior
    assert origin["starting_branch"] == branch
    assert origin["inherited_work"] is True
    assert "Continuation of earlier attempt" in task.spec
    assert prior in task.spec
    assert branch in task.spec
    assert task.root_spec == "implement feature"
    assert read_origin(project, task.id)["inherited_work"] is True


def test_fresh_preserves_previous_branch_worktree_and_records(project, capsys):
    old = setup_attempt(project, "task_abc", "implement feature")
    with Repo(old) as repo:
        (Path(old) / "feature.txt").write_text("halted work")
        repo.index.add(["feature.txt"])
        prior = repo.index.commit("halted").hexsha
        branch = repo.active_branch.name
    records = Path(project) / ".snodo" / "tasks" / "task_abc"
    records.mkdir(parents=True, exist_ok=True)
    evidence = records / "previous.json"
    evidence.write_text(json.dumps({"commit": prior}))
    fresh = setup_attempt(project, "task_abc", "implement feature", fresh_start=True)
    assert fresh != old
    assert (Path(old) / "feature.txt").read_text() == "halted work"
    assert not (Path(fresh) / "feature.txt").exists()
    with Repo(project) as repo:
        assert repo.heads[branch].commit.hexsha == prior
        base = repo.commit("main").hexsha
    with Repo(fresh) as repo:
        assert repo.head.commit.hexsha == base
    task = Task(id="task_abc", spec="implement feature")
    origin = record_origin(project, task, fresh)
    assert origin["starting_revision"] == base
    assert origin["inherited_work"] is False
    assert "Continuation" not in task.spec
    assert json.loads(evidence.read_text()) == {"commit": prior}
    assert "records left untouched" in capsys.readouterr().out
    assert setup_attempt(project, task.id, task.spec) == fresh
    assert workspace_identity(task.id, fresh) == Path(fresh).name
    worktree.teardown_task_worktree(project, workspace_identity(task.id, fresh))
    with Repo(project) as repo:
        assert branch in repo.heads
    assert Path(old).exists()


def test_fresh_leaves_unregistered_directory_exactly_alone(project):
    unowned = worktree.worktree_path(project, "task_abc")
    unowned.mkdir(parents=True)
    (unowned / "evidence.txt").write_text("not ours")
    assert not worktree.worktree_is_owned(project, "task_abc")
    fresh = setup_attempt(project, "task_abc", "implement feature", fresh_start=True)
    task = Task(id="task_abc", spec="implement feature")
    origin = record_origin(project, task, fresh)
    assert origin["inherited_work"] is False
    assert origin["starting_revision"]
    assert read_origin(project, task.id)["inherited_work"] is False
    assert (unowned / "evidence.txt").read_text() == "not ours"
    assert fresh != str(unowned)


def test_plan_fresh_uses_task_base(project):
    with Repo(project) as repo:
        repo.create_head("plan/base")
    path = setup_attempt(project, "1.1_feature", "implement feature", fresh_start=True,
                         plan_name="example", base="plan/base")
    task = Task(id="1.1_feature", spec="implement feature", plan_name="example")
    origin = record_origin(project, task, path, "example", "plan/base")
    assert origin["inherited_work"] is False
    assert worktree.worktree_is_owned(project, Path(path).name, "example")


def test_origin_unknown_and_display(project, capsys):
    task = Task(id="task_missing", spec="request")
    origin = record_origin(project, task, None)
    assert origin["starting_revision"] is None
    assert origin["inherited_work"] is False
    print_origin(read_origin(project, task.id))
    assert "Starting revision: unknown" in capsys.readouterr().out
    assert read_origin(project, "absent") == {}
    state = Path(project) / ".snodo/tasks/task_missing/state.json"
    state.write_text("[]")
    assert read_origin(project, task.id) == {}


def test_halted_origin_is_displayed_and_survives_a_fresh_attempt(project, monkeypatch, capsys):
    from snodo.cli.commands.task_cmd import task_show_command
    from snodo.cli.commands.task_record import _record_task_start, _record_task_completion
    from snodo.infrastructure.session import SessionManager
    from snodo.infrastructure.state import ProjectState, write_state

    task = Task(id="task_abc", spec="implement feature")
    path = setup_attempt(project, task.id, task.spec)
    with Repo(path) as repo:
        (Path(path) / "feature.txt").write_text("earlier attempt")
        repo.index.add(["feature.txt"])
        prior = repo.index.commit("halted work").hexsha
    _record_task_start(project, task.id, task.spec)
    origin = record_origin(project, task, path)
    halt = {
        "task_id": task.id, "task_spec": task.root_spec,
        "status": "blocked", "final_decision": "blocked",
        "halt_type": "validation_blocker", "reason": "needs changes",
    }
    _record_task_completion(project, task.id, "blocked", halt)
    directory = Path(project) / ".snodo" / "tasks" / task.id
    recorded = json.loads((directory / "state.json").read_text())
    assert recorded["halt"]["starting_revision"] == prior
    assert recorded["halt"]["inherited_work"] is True

    write_state(project, ProjectState(current_mode="producer"))
    manager = SessionManager(sessions_dir=Path(project) / ".snodo" / "sessions")
    session = manager.create_session("producer", project)
    manager.update_decision(session.session_id, "halt", {task.id: halt})
    monkeypatch.setattr("snodo.cli.commands.task_cmd.resolve_project_root", lambda: project)
    monkeypatch.setattr("snodo.infrastructure.paths.resolve_project_root", lambda: project)
    capsys.readouterr()
    with patch("snodo.infrastructure.session.SessionManager", return_value=manager):
        assert task_show_command(SimpleNamespace(task_id=task.id, json=False)) == 0
    output = capsys.readouterr().out
    assert f"Starting revision: {prior}" in output
    assert "Inherited work: True" in output

    _record_task_start(project, task.id, task.root_spec)
    fresh = setup_attempt(project, task.id, task.root_spec, fresh_start=True)
    new_task = Task(id=task.id, spec=task.root_spec)
    fresh_origin = record_origin(project, new_task, fresh)
    assert fresh_origin["inherited_work"] is False
    snapshots = [json.loads(p.read_text()) for p in (directory / "attempts").glob("*.json")]
    assert len(snapshots) == 1
    assert snapshots[0]["status"] == "blocked"
    for key in ("starting_revision", "starting_branch", "inherited_work"):
        assert snapshots[0][key] == origin[key]
