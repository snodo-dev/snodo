"""Test that investigation tasks without diff record findings in the task record.

A task whose deliverable is findings (survey, investigation, recommendation)
produces no diff. The engine must allow such a task to complete and record
its findings in the task record under .snodo/tasks/<task_id>/state.json.
"""

import json

from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Coder, CodeArtifact
from snodo.engine.loop import LoopStage, build_protocol_graph
from snodo.tools.git import open_repo


class InvestigationCoder(Coder):
    """A coder that performs an investigation and returns findings with no files changed."""

    def __init__(self, findings: str):
        self.findings = findings
        self.skip_workspace_write = False
        self.skip_engine_commit = False

    def implement(self, spec) -> CodeArtifact:
        # Returns no file operations, but provides substantive findings
        artifact = CodeArtifact(files=[])
        artifact.metadata["findings"] = self.findings
        # Also simulate coder report if supported
        from snodo.coders.report import CoderReport
        try:
            self.last_report = CoderReport(stop_reason="completed", findings=self.findings)
        except Exception:
            # Current code might reject findings in CoderReport
            self.last_report = CoderReport(stop_reason="completed")
        return artifact


def _investigation_protocol() -> Protocol:
    return Protocol(
        protocol_id="investigation_proto",
        name="Investigation Protocol",
        version="1.0.0",
        initial_mode="survey",
        modes=[
            Mode(
                mode_id="survey",
                name="Survey Mode",
                tools=["read"],
                validators=["v1"],
            )
        ],
        validators=[
            Validator(
                validator_id="v1",
                validator_type="security",
                evaluation_phase="pre_execute",
            )
        ],
    )


def test_investigation_task_without_diff_records_findings(tmp_path):
    """A task with findings and no file changes completes and records findings in state.json."""
    project_root = tmp_path / "repo"
    project_root.mkdir()

    # Initialize a clean git repo
    from git import Repo
    repo = Repo.init(str(project_root))
    (project_root / "README.md").write_text("# Test Repo\n")
    (project_root / ".gitignore").write_text(".snodo/\n")
    repo.git.add("-A")
    repo.git.commit("-m", "initial commit", env={
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    })

    snodo_tasks_dir = project_root / ".snodo" / "tasks"
    snodo_tasks_dir.mkdir(parents=True)

    task_id = "task_survey_01"
    findings_content = "Survey findings: src/auth.py:42 needs refactoring; src/tokens.py:10 is clean."
    coder = InvestigationCoder(findings=findings_content)

    def pass_validator(task, validators, shell, **kwargs):
        from snodo.core.interfaces import ValidatorResult
        return [
            ValidatorResult(validator_id=v.validator_id, severity="pass", justification="ok")
            for v in validators
        ]

    protocol = _investigation_protocol()
    graph = build_protocol_graph(
        protocol,
        project_root=str(project_root),
        coder=coder,
        validator_fn=pass_validator,
    )
    compiled = graph.compile()

    # Initialize task state under .snodo/tasks/<task_id>/state.json
    task_dir = snodo_tasks_dir / task_id
    task_dir.mkdir()
    (task_dir / "state.json").write_text(json.dumps({
        "task_id": task_id,
        "status": "running",
        "description": "Survey the auth system",
    }))

    initial_state = {
        "task": {"id": task_id, "spec": "Survey the auth system"},
        "current_mode": "survey",
        "iteration": 0,
        "stage": "governance",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {},
    }

    result = compiled.invoke(initial_state)

    # 1. The task must complete successfully without blocking/failing
    assert result["stage"] == LoopStage.COMPLETE.value, (
        f"Expected task to complete, but stage is {result['stage']}. "
        f"Constraint violations: {result.get('constraint_violations')}"
    )
    assert not result["is_blocked"], "Task was unexpectedly blocked"

    # 2. No repository files were changed (no diff produced)
    with open_repo(str(project_root)) as repo:
        assert not repo.is_dirty(untracked_files=True), "Working tree should have no changes"

    # 3. Findings must be retrievable from the task record under .snodo/tasks/<task_id>/state.json
    task_state_file = task_dir / "state.json"
    assert task_state_file.is_file(), f"Task state file missing: {task_state_file}"
    task_record = json.loads(task_state_file.read_text())

    # Findings should be present at top-level or in halt payload
    retrieved_findings = task_record.get("findings") or (task_record.get("halt") or {}).get("findings")
    assert retrieved_findings is not None, f"Findings not found in task record: {task_record}"
    assert findings_content in str(retrieved_findings)
