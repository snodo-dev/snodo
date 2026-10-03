"""Real-Git regression coverage for each Snodo merge and delivery boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def _repository(tmp_path, *, remote=False):
    from git import Repo

    project = tmp_path / "project"
    project.mkdir()
    repo = Repo.init(project, initial_branch="main")
    repo.config_writer().set_value("user", "name", "Snodo Test").release()
    repo.config_writer().set_value("user", "email", "snodo@example.invalid").release()
    (project / "base.txt").write_text("base\n")
    repo.index.add(["base.txt"])
    base = repo.index.commit("base").hexsha
    if remote:
        bare = Repo.init(tmp_path / "remote.git", bare=True)
        repo.create_remote("origin", str(tmp_path / "remote.git"))
        repo.git.push("-u", "origin", "main")
        bare.close()
    repo.close()
    return project, base


def _branch(project, base, branch, filename):
    from git import Repo

    path = project / f"checkout-{filename}"
    with Repo(str(project)) as repo:
        repo.git.worktree("add", "-b", branch, str(path), base)
    with Repo(str(path)) as worktree:
        (path / filename).write_text("change\n")
        worktree.index.add([filename])
        commit = worktree.index.commit(f"add {filename}").hexsha
    return path, commit


def _verification(audit, task_ref, commit):
    audit.append_event("verification_executed", {
        "op": "verification_executed", "task_ref": task_ref,
        "validator_id": "quality", "returncode": 0, "commit": commit,
        "outcome": "pass", "command": "true",
    })


def _assert_blocked(audit, result):
    assert result[0] != 0
    events = audit.get_history("unverified_merge_blocked")
    assert events
    assert events[-1].data["op"] == "unverified_merge_blocked"


@pytest.mark.parametrize("scope", ["standalone", "plan_task"])
def test_task_branch_merge_requires_exact_commit_verification(tmp_path, scope):
    from snodo.cli.commands.run_merge import _merge_on_success
    from snodo.core.interfaces import Task
    from snodo.infrastructure.audit import AuditLog
    from git import Repo

    project, base = _repository(tmp_path)
    task = Task(id="task_gate", spec="add a file")
    plan_name = "plan-gate" if scope == "plan_task" else None
    from snodo.infrastructure.worktree import task_branch_name
    branch = task_branch_name(task.id, task.spec, plan_name)
    integration_branch = "plan/plan-gate/integration"
    if plan_name:
        _branch(project, base, integration_branch, "integration.txt")
    _path, commit = _branch(project, base, branch, "task.txt")
    audit = AuditLog(str(tmp_path / "audit.log"))

    if plan_name:
        import os
        previous = os.environ.get("SNODO_PLAN_INTEGRATION_BRANCH")
        os.environ["SNODO_PLAN_INTEGRATION_BRANCH"] = integration_branch
    try:
        _assert_blocked(audit, _merge_on_success(str(project), task, 0, None, audit, plan_name=plan_name))
    finally:
        if plan_name:
            if previous is None:
                os.environ.pop("SNODO_PLAN_INTEGRATION_BRANCH", None)
            else:
                os.environ["SNODO_PLAN_INTEGRATION_BRANCH"] = previous
    with Repo(str(project)) as repo:
        assert repo.commit("main").hexsha == base

    _verification(audit, task.id, commit)
    if plan_name:
        import os
        os.environ["SNODO_PLAN_INTEGRATION_BRANCH"] = integration_branch
    try:
        assert _merge_on_success(str(project), task, 0, None, audit, plan_name=plan_name)[0] == 0
    finally:
        if plan_name:
            if previous is None:
                os.environ.pop("SNODO_PLAN_INTEGRATION_BRANCH", None)
            else:
                os.environ["SNODO_PLAN_INTEGRATION_BRANCH"] = previous
    with Repo(str(project)) as repo:
        target = integration_branch if plan_name else "main"
        assert "task.txt" in repo.git.ls_tree("-r", target)


def test_custom_quality_validator_id_is_accepted_without_loosening_evidence(tmp_path):
    from snodo.cli.commands.run_merge import _merge_on_success
    from snodo.compiler.models import Validator
    from snodo.core.interfaces import Task
    from snodo.infrastructure.audit import AuditLog

    project, base = _repository(tmp_path)
    task = Task(id="task_custom_gate", spec="add a file")
    from snodo.infrastructure.worktree import task_branch_name
    branch = task_branch_name(task.id, task.spec)
    _path, commit = _branch(project, base, branch, "task.txt")
    audit = AuditLog(str(tmp_path / "audit.log"))
    protocol = SimpleNamespace(validators=[Validator(
        validator_id="qa-gate", validator_type="quality",
    )])

    def record(task_ref, sha, outcome="pass", validator_id="qa-gate"):
        audit.append_event("verification_executed", {
            "op": "verification_executed", "task_ref": task_ref,
            "validator_id": validator_id, "returncode": 0 if outcome == "pass" else 1,
            "commit": sha, "outcome": outcome, "command": "true",
        })

    for task_ref, sha, outcome in (
        ("other-task", commit, "pass"),
        (task.id, "f" + commit[1:], "pass"),
        (task.id, commit, "fail"),
    ):
        record(task_ref, sha, outcome)
        assert _merge_on_success(
            str(project), task, 0, None, audit, protocol=protocol,
        )[0] != 0

    record(task.id, commit)
    assert _merge_on_success(
        str(project), task, 0, None, audit, protocol=protocol,
    )[0] == 0


def test_default_quality_validator_id_remains_accepted(tmp_path):
    from snodo.cli.commands.run_merge import _merge_on_success
    from snodo.compiler.models import Validator
    from snodo.core.interfaces import Task
    from snodo.infrastructure.audit import AuditLog

    project, base = _repository(tmp_path)
    task = Task(id="task_default_gate", spec="add a file")
    from snodo.infrastructure.worktree import task_branch_name
    branch = task_branch_name(task.id, task.spec)
    _path, commit = _branch(project, base, branch, "task.txt")
    audit = AuditLog(str(tmp_path / "audit.log"))
    protocol = SimpleNamespace(validators=[Validator(
        validator_id="quality", validator_type="quality",
    )])
    _verification(audit, task.id, commit)
    assert _merge_on_success(
        str(project), task, 0, None, audit, protocol=protocol,
    )[0] == 0


def test_fast_path_merge_uses_protocol_validator_and_delivery(tmp_path):
    from git import Repo
    from snodo.cli.commands.run_merge import _try_merge_unmerged_task
    from snodo.compiler.models import Validator
    from snodo.infrastructure.audit import AuditLog
    from snodo.infrastructure.worktree import task_branch_name

    project, base = _repository(tmp_path, remote=True)
    task_id, spec = "task_fast_path", "fast path delivery"
    branch = task_branch_name(task_id, spec)
    _path, commit = _branch(project, base, branch, "fast.txt")
    audit = AuditLog(str(tmp_path / "audit.log"))
    protocol = SimpleNamespace(
        validators=[Validator(validator_id="qa-gate", validator_type="quality")],
        initial_mode="producer",
        delivery_for=lambda mode: "push_branch" if mode == "producer" else "local_merge",
        execution=SimpleNamespace(delivery_remote="origin"),
        metadata={"provider": "test"},
    )
    audit.append_event("verification_executed", {
        "op": "verification_executed", "task_ref": task_id,
        "validator_id": "qa-gate", "returncode": 0, "commit": commit,
        "outcome": "pass", "command": "true",
    })

    assert _try_merge_unmerged_task(
        str(project), task_id, spec, protocol=protocol, audit_log=audit,
    ) is True
    with Repo(str(project)) as repo:
        assert repo.commit("main").hexsha == base
    with Repo(str(tmp_path / "remote.git")) as remote:
        assert remote.commit(branch).hexsha == commit


def test_fast_path_refuses_without_passing_custom_validator_evidence(tmp_path):
    from snodo.cli.commands.run_merge import _try_merge_unmerged_task
    from snodo.compiler.models import Validator
    from snodo.infrastructure.audit import AuditLog
    from snodo.infrastructure.worktree import task_branch_name

    project, base = _repository(tmp_path)
    task_id, spec = "task_fast_refused", "fast path refusal"
    branch = task_branch_name(task_id, spec)
    _path, commit = _branch(project, base, branch, "fast.txt")
    audit = AuditLog(str(tmp_path / "audit.log"))
    audit.append_event("verification_executed", {
        "op": "verification_executed", "task_ref": task_id,
        "validator_id": "qa-gate", "returncode": 0, "commit": commit,
        "outcome": "fail", "command": "false",
    })
    protocol = SimpleNamespace(
        validators=[Validator(validator_id="qa-gate", validator_type="quality")],
        initial_mode="producer", delivery_for=lambda _mode: "local_merge",
    )
    assert _try_merge_unmerged_task(
        str(project), task_id, spec, protocol=protocol, audit_log=audit,
    ) is None


@pytest.mark.parametrize("delivery", ["local_merge", "push_branch", "change_request"])
def test_plan_integration_delivery_requires_exact_commit_verification(tmp_path, delivery, monkeypatch):
    from git import Repo
    from snodo.cli.commands.run_merge import _deliver_plan_integration
    from snodo.infrastructure.audit import AuditLog

    project, base = _repository(tmp_path, remote=delivery != "local_merge")
    branch = "plan/demo/integration"
    integration, commit = _branch(project, base, branch, "combined.txt")
    audit = AuditLog(str(tmp_path / "audit.log"))
    from snodo.compiler.models import Validator
    quality = Validator(validator_id="qa-gate", validator_type="quality", tooling={"test_command": "false"})
    protocol = SimpleNamespace(
        delivery_for=lambda _mode: delivery,
        execution=SimpleNamespace(delivery_remote="origin"),
        metadata={"provider": "test"}, validators=[quality],
    )
    if delivery == "change_request":
        class Provider:
            def create_change_request(self, *_args, **_kwargs):
                return "test-change-request"

        monkeypatch.setattr("snodo.providers.registry.detect_provider", lambda *_: Provider())

    result = _deliver_plan_integration(
        str(project), branch, "demo", "intent", protocol, "producer", audit,
        integration_path=integration,
    )
    assert result == 2
    assert audit.get_history("unverified_merge_blocked")
    quality = quality.model_copy(update={"tooling": {"test_command": "true"}})
    protocol.validators = [quality]
    assert _deliver_plan_integration(
        str(project), branch, "demo", "intent", protocol, "producer", audit,
        integration_path=integration,
    ) == 0
    if delivery == "local_merge":
        with Repo(str(project)) as repo:
            assert "combined.txt" in repo.git.ls_tree("-r", "HEAD")
    else:
        with Repo(str(tmp_path / "remote.git")) as remote:
            assert remote.commit(branch).hexsha == commit


def test_queue_integration_merge_requires_verification_of_queue_head(tmp_path):
    from git import Repo
    from snodo.cli.commands.plan_run import _verify_queue_merge_head
    from snodo.cli.commands.run_merge import _merge_on_success
    from snodo.core.interfaces import Task
    from snodo.infrastructure.audit import AuditLog
    from snodo.compiler.models import Validator

    project, base = _repository(tmp_path)
    branch = "queue/default/integration"
    _path, commit = _branch(project, base, branch, "queued.txt")
    audit = AuditLog(str(tmp_path / "audit.log"))
    quality = Validator(validator_id="qa-gate", validator_type="quality", tooling={"test_command": "false"})
    protocol = SimpleNamespace(validators=[quality])

    assert not _verify_queue_merge_head(project, project, "queue:default", protocol, audit, branch)
    assert audit.get_history("unverified_merge_blocked")
    with Repo(str(project)) as repo:
        assert repo.commit("main").hexsha == base

    audit.append_event("verification_executed", {
        "op": "verification_executed", "task_ref": "queue:default",
        "validator_id": "qa-gate", "returncode": 0, "commit": commit,
        "outcome": "pass", "command": "true",
    })
    assert _verify_queue_merge_head(project, project, "queue:default", protocol, audit, branch)
    task = Task(id="queue:default", spec="queue integration")
    merged = _merge_on_success(
        project, task, 0, None, audit, branch_override=branch, protocol=protocol,
    )
    assert merged[0] == 0
    with Repo(str(project)) as repo:
        assert "queued.txt" in repo.git.ls_tree("-r", "HEAD")
    verified = [
        event for event in audit.get_history("verification_executed")
        if event.data.get("commit") == commit and event.data.get("outcome") == "pass"
    ]
    assert verified
