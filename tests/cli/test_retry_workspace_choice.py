"""Additive coverage for retry origin and clean-start selection."""

import json
from pathlib import Path

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


def test_clean_start_surfaces_share_default_and_name():
    import typer
    from typer.main import get_group
    from snodo.cli.commands.run_cmd import register, RunArgs
    from snodo.cli.commands.plan_cmd import app
    from snodo.mcp.tools import TOOL_REGISTRY

    run_app = typer.Typer()
    register(run_app)
    commands = [get_group(run_app).commands["run"], get_group(app).commands["run"]]
    for command in commands:
        option = next(p for p in command.params if p.name == "fresh_start")
        assert "--fresh-start" in option.opts
        assert option.default is False
        assert "previous" in option.help
    assert RunArgs().fresh_start is False
    schema = TOOL_REGISTRY["retry_job"]["inputSchema"]["properties"]["fresh_start"]
    assert schema["type"] == "boolean"
    assert "default false" in schema["description"]
