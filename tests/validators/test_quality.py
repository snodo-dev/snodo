"""Tests for QualityValidator and evaluation phases (Task 3.7 + 6.2).

FILE: tests/validators/test_quality.py

Covers:
- QualityValidator: language-agnostic test suite execution
- Auto-detection of test commands
- Tooling config from protocol YAML
- Validator model: evaluation_phase, tooling fields
- Loop: phase-aware validation, post_validate node, quality dispatch
- Protocol: get_validators_by_phase()
"""

import tempfile
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from snodo.compiler.models import (
    Protocol, Mode, Validator, DisagreementPolicy
)
from snodo.core.interfaces import ValidatorResult
from snodo.engine.loop import GraphBuilder, LoopStage, build_protocol_graph
from snodo.validators.quality import QualityValidator


# === Fixtures ===

@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp()
    # Init git repo
    subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, capture_output=True, check=True)
    readme = Path(d) / "README.md"
    readme.write_text("test")
    subprocess.run(["git", "add", "."], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=d, capture_output=True, check=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def quality_spec():
    """A quality validator spec with tooling config."""
    return Validator(
        validator_id="quality",
        validator_type="quality",
        evaluation_phase="post_execute",
        tooling={"test_command": "pytest -q"},
    )


@pytest.fixture
def quality_spec_no_tooling():
    """A quality validator spec without tooling config."""
    return Validator(
        validator_id="quality",
        validator_type="quality",
        evaluation_phase="post_execute",
    )


@pytest.fixture
def qv(quality_spec, project_dir):
    return QualityValidator(quality_spec, project_dir)


@pytest.fixture
def phased_protocol():
    """Protocol with both pre_execute and post_execute validators."""
    return Protocol(
        protocol_id="phased",
        name="Phased Protocol",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit", "test"],
                validators=["security", "quality"],
            ),
        ],
        validators=[
            Validator(
                validator_id="security",
                validator_type="security",
                evaluation_phase="pre_execute",
                criteria=["Check security"],
            ),
            Validator(
                validator_id="quality",
                validator_type="quality",
                evaluation_phase="post_execute",
                tooling={"test_command": "echo tests pass"},
            ),
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )


# === Validator Model: evaluation_phase ===

class TestEvaluationPhase:
    def test_default_phase_is_pre_execute(self):
        v = Validator(validator_id="v1", validator_type="security")
        assert v.evaluation_phase == "pre_execute"

    def test_explicit_pre_execute(self):
        v = Validator(validator_id="v1", validator_type="security", evaluation_phase="pre_execute")
        assert v.evaluation_phase == "pre_execute"

    def test_explicit_post_execute(self):
        v = Validator(validator_id="v1", validator_type="quality", evaluation_phase="post_execute")
        assert v.evaluation_phase == "post_execute"

    def test_mode_transition_phase_allowed(self):
        v = Validator(validator_id="v1", validator_type="security", evaluation_phase="mode_transition")
        assert v.evaluation_phase == "mode_transition"

    def test_invalid_phase_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="evaluation_phase must be one of"):
            Validator(validator_id="v1", validator_type="security", evaluation_phase="during_review")

    def test_empty_phase_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="evaluation_phase must be one of"):
            Validator(validator_id="v1", validator_type="security", evaluation_phase="")

    def test_quality_validator_type_valid(self):
        v = Validator(validator_id="q1", validator_type="quality")
        assert v.validator_type == "quality"

    def test_backward_compat_no_phase_in_dict(self):
        """Validators created from dicts without evaluation_phase still work."""
        v = Validator(**{"validator_id": "v1", "validator_type": "security"})
        assert v.evaluation_phase == "pre_execute"

    def test_yaml_roundtrip_with_phase(self):
        v = Validator(
            validator_id="v1",
            validator_type="quality",
            evaluation_phase="post_execute",
        )
        d = v.model_dump()
        assert d["evaluation_phase"] == "post_execute"
        v2 = Validator(**d)
        assert v2.evaluation_phase == "post_execute"


# === Validator Model: tooling field ===

class TestToolingField:
    def test_tooling_default_empty(self):
        v = Validator(validator_id="v1", validator_type="quality")
        assert v.tooling == {}

    def test_tooling_with_test_command(self):
        v = Validator(
            validator_id="v1",
            validator_type="quality",
            tooling={"test_command": "npm test"},
        )
        assert v.tooling["test_command"] == "npm test"

    def test_tooling_with_timeout(self):
        v = Validator(
            validator_id="v1",
            validator_type="quality",
            tooling={"test_command": "pytest", "timeout": 120},
        )
        assert v.tooling["timeout"] == 120

    def test_tooling_roundtrip(self):
        v = Validator(
            validator_id="v1",
            validator_type="quality",
            tooling={"test_command": "cargo test"},
        )
        d = v.model_dump()
        assert d["tooling"]["test_command"] == "cargo test"
        v2 = Validator(**d)
        assert v2.tooling["test_command"] == "cargo test"

    def test_backward_compat_no_tooling(self):
        """Old protocol dicts without tooling field still work."""
        v = Validator(**{"validator_id": "v1", "validator_type": "security"})
        assert v.tooling == {}


# === Protocol.get_validators_by_phase ===

class TestGetValidatorsByPhase:
    def test_pre_execute_validators(self, phased_protocol):
        pre = phased_protocol.get_validators_by_phase("pre_execute")
        assert len(pre) == 1
        assert pre[0].validator_id == "security"

    def test_post_execute_validators(self, phased_protocol):
        post = phased_protocol.get_validators_by_phase("post_execute")
        assert len(post) == 1
        assert post[0].validator_id == "quality"

    def test_nonexistent_phase_returns_empty(self, phased_protocol):
        result = phased_protocol.get_validators_by_phase("during_review")
        assert result == []

    def test_all_default_phase(self):
        p = Protocol(
            protocol_id="test",
            name="Test",
            modes=[Mode(mode_id="m1", name="M")],
            validators=[
                Validator(validator_id="v1", validator_type="security"),
                Validator(validator_id="v2", validator_type="architecture"),
            ],
            initial_mode="m1",
        )
        pre = p.get_validators_by_phase("pre_execute")
        assert len(pre) == 2
        post = p.get_validators_by_phase("post_execute")
        assert len(post) == 0


# === QualityValidator: test command resolution ===

class TestTestCommandResolution:
    def test_uses_tooling_config(self, quality_spec, project_dir):
        qv = QualityValidator(quality_spec, project_dir)
        assert qv._resolve_test_command() == "pytest -q"

    def test_auto_detect_python(self, quality_spec_no_tooling, project_dir):
        (Path(project_dir) / "pyproject.toml").write_text("[project]\nname='test'\n")
        qv = QualityValidator(quality_spec_no_tooling, project_dir)
        assert qv._resolve_test_command() == "pytest"

    def test_auto_detect_node(self, quality_spec_no_tooling, project_dir):
        (Path(project_dir) / "package.json").write_text('{"name": "test"}')
        qv = QualityValidator(quality_spec_no_tooling, project_dir)
        assert qv._resolve_test_command() == "npm test"

    def test_auto_detect_rust(self, quality_spec_no_tooling, project_dir):
        (Path(project_dir) / "Cargo.toml").write_text("[package]\nname='test'\n")
        qv = QualityValidator(quality_spec_no_tooling, project_dir)
        assert qv._resolve_test_command() == "cargo test"

    def test_auto_detect_go(self, quality_spec_no_tooling, project_dir):
        (Path(project_dir) / "go.mod").write_text("module example.com/test\n")
        qv = QualityValidator(quality_spec_no_tooling, project_dir)
        assert qv._resolve_test_command() == "go test ./..."

    def test_auto_detect_makefile(self, quality_spec_no_tooling, project_dir):
        (Path(project_dir) / "Makefile").write_text("test:\n\techo ok\n")
        qv = QualityValidator(quality_spec_no_tooling, project_dir)
        assert qv._resolve_test_command() == "make test"

    def test_no_detection_returns_none(self, quality_spec_no_tooling, project_dir):
        qv = QualityValidator(quality_spec_no_tooling, project_dir)
        # project_dir has no marker files (only README.md)
        assert qv._resolve_test_command() is None

    def test_tooling_overrides_auto_detect(self, project_dir):
        """Tooling config takes precedence over auto-detection."""
        (Path(project_dir) / "pyproject.toml").write_text("[project]\nname='test'\n")
        spec = Validator(
            validator_id="quality",
            validator_type="quality",
            tooling={"test_command": "custom-test-runner"},
        )
        qv = QualityValidator(spec, project_dir)
        assert qv._resolve_test_command() == "custom-test-runner"


# === QualityValidator: evaluate ===

class TestQualityValidatorEvaluate:
    def test_tests_pass(self, quality_spec, project_dir):
        qv = QualityValidator(quality_spec, project_dir)
        mock_result = MagicMock(returncode=0, stdout="5 passed\n", stderr="")
        with patch("snodo.validators.quality.subprocess.run", return_value=mock_result):
            result = qv.evaluate()
            assert result.severity == "pass"
            assert result.validator_id == "quality"
            assert "passed" in result.justification.lower()

    def test_tests_fail_is_blocker(self, quality_spec, project_dir):
        """Test failures are blockers (NOT downgraded to warn)."""
        qv = QualityValidator(quality_spec, project_dir)
        mock_result = MagicMock(returncode=1, stdout="2 failed, 3 passed\n", stderr="")
        with patch("snodo.validators.quality.subprocess.run", return_value=mock_result):
            result = qv.evaluate()
            assert result.severity == "blocker"
            assert "failed" in result.justification.lower()

    def test_command_not_found_warns(self, quality_spec, project_dir):
        qv = QualityValidator(quality_spec, project_dir)
        with patch("snodo.validators.quality.subprocess.run", side_effect=FileNotFoundError):
            result = qv.evaluate()
            assert result.severity == "warn"
            assert "not found" in result.justification.lower()

    def test_timeout_is_blocker(self, quality_spec, project_dir):
        qv = QualityValidator(quality_spec, project_dir)
        with patch(
            "snodo.validators.quality.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="pytest", timeout=300),
        ):
            result = qv.evaluate()
            assert result.severity == "blocker"
            assert "timed out" in result.justification

    def test_no_test_command_warns(self, quality_spec_no_tooling, project_dir):
        """When no test command can be determined, return warn."""
        qv = QualityValidator(quality_spec_no_tooling, project_dir)
        result = qv.evaluate()
        assert result.severity == "warn"
        assert "Cannot determine" in result.justification

    def test_custom_timeout_from_tooling(self, project_dir):
        spec = Validator(
            validator_id="quality",
            validator_type="quality",
            tooling={"test_command": "echo ok", "timeout": 60},
        )
        qv = QualityValidator(spec, project_dir)
        assert qv._get_timeout() == 60.0

    def test_default_timeout(self, quality_spec, project_dir):
        qv = QualityValidator(quality_spec, project_dir)
        assert qv._get_timeout() == 300.0

    def test_stderr_used_when_no_stdout(self, quality_spec, project_dir):
        qv = QualityValidator(quality_spec, project_dir)
        mock_result = MagicMock(returncode=1, stdout="", stderr="Error: compilation failed\n")
        with patch("snodo.validators.quality.subprocess.run", return_value=mock_result):
            result = qv.evaluate()
            assert result.severity == "blocker"
            assert "compilation failed" in result.justification


class TestExtractSummary:
    def test_last_line(self, quality_spec, project_dir):
        qv = QualityValidator(quality_spec, project_dir)
        assert qv._extract_summary("line1\nline2\nline3") == "line3"

    def test_empty_returns_no_output(self, quality_spec, project_dir):
        qv = QualityValidator(quality_spec, project_dir)
        assert qv._extract_summary("") == "no output"
        assert qv._extract_summary("   ") == "no output"

    def test_truncates_long_lines(self, quality_spec, project_dir):
        qv = QualityValidator(quality_spec, project_dir)
        long_line = "x" * 300
        assert len(qv._extract_summary(long_line)) == 200


# === Loop Phase-Aware Validation ===

class TestLoopPhases:
    def test_graph_has_post_validate_node(self, phased_protocol, project_dir):
        builder = GraphBuilder(phased_protocol)
        graph = builder.build_graph()
        assert "post_validate" in graph.nodes

    def test_pre_execute_filters_validators(self, phased_protocol, project_dir):
        """validate node only runs pre_execute validators."""
        call_log = []

        def tracking_validator(task, validators, shell_mcp, current_mode="", **kwargs):
            call_log.append([v.validator_id for v in validators])
            return [
                ValidatorResult(validator_id=v.validator_id, severity="pass", justification="stub")
                for v in validators
            ]

        builder = GraphBuilder(
            phased_protocol,
            validator_fn=tracking_validator,
        )

        state = {
            "task": {"id": "t1", "spec": "test"},
            "current_mode": "producer",
            "iteration": 1,
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

        builder._validate_node(state)
        # Only pre_execute validator (security) should be passed
        assert call_log[0] == ["security"]

    def test_post_validate_runs_post_execute_validators(self, phased_protocol, project_dir):
        """post_validate node runs post_execute validators."""
        call_log = []

        def tracking_validator(task, validators, shell_mcp, current_mode="", **kwargs):
            call_log.append([v.validator_id for v in validators])
            return [
                ValidatorResult(validator_id=v.validator_id, severity="pass", justification="stub")
                for v in validators
            ]

        builder = GraphBuilder(
            phased_protocol,
            validator_fn=tracking_validator,
        )

        state = {
            "task": {"id": "t1", "spec": "test"},
            "current_mode": "producer",
            "iteration": 1,
            "stage": "execute",
            "validation_results": [
                {"validator_id": "security", "severity": "pass", "justification": "ok"},
            ],
            "validation_token": {
                "task_id": "t1",
                "signatures": ["security"],
                "timestamp": "now",
            },
            "artifacts": ["src/hello.py"],
            "constraints_passed": True,
            "constraint_violations": [],
            "policy_decision": None,
            "is_complete": False,
            "is_blocked": False,
            "metadata": {},
        }

        result = builder._post_validate_node(state)
        # Quality validator (post_execute) should be passed
        assert call_log[0] == ["quality"]
        # Results merged
        assert len(result["validation_results"]) == 2

    def test_post_validate_blocker_blocks(self, phased_protocol, project_dir):
        """post_validate with blocker result blocks execution."""
        def blocker_validator(task, validators, shell_mcp, current_mode="", **kwargs):
            return [
                ValidatorResult(validator_id=v.validator_id, severity="blocker", justification="quality failed")
                for v in validators
            ]

        builder = GraphBuilder(
            phased_protocol,
            validator_fn=blocker_validator,
        )

        state = {
            "task": {"id": "t1", "spec": "test"},
            "current_mode": "producer",
            "iteration": 1,
            "stage": "execute",
            "validation_results": [],
            "validation_token": {
                "task_id": "t1",
                "signatures": ["security"],
                "timestamp": "now",
            },
            "artifacts": [],
            "constraints_passed": True,
            "constraint_violations": [],
            "policy_decision": None,
            "is_complete": False,
            "is_blocked": False,
            "metadata": {},
        }

        result = builder._post_validate_node(state)
        assert result["is_blocked"] is True
        assert any("Post-execute" in v for v in result["constraint_violations"])

    def test_post_validate_no_validators_passes_through(self, project_dir):
        """If no post_execute validators, post_validate is a pass-through."""
        protocol = Protocol(
            protocol_id="pre_only",
            name="Pre Only",
            modes=[
                Mode(mode_id="m1", name="M", validators=["v1"]),
            ],
            validators=[
                Validator(validator_id="v1", validator_type="security",
                          evaluation_phase="pre_execute"),
            ],
            initial_mode="m1",
        )

        builder = GraphBuilder(protocol)
        state = {
            "task": {"id": "t1", "spec": "test"},
            "current_mode": "m1",
            "iteration": 1,
            "stage": "execute",
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

        result = builder._post_validate_node(state)
        assert result["is_blocked"] is False

    def test_route_after_post_validation_proceed(self, phased_protocol):
        builder = GraphBuilder(phased_protocol)
        state = {
            "task": {"id": "t1", "spec": "test"},
            "current_mode": "producer",
            "iteration": 1,
            "stage": "validate",
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
        assert builder._route_after_post_validation(state) == "move_next"

    def test_route_after_post_validation_blocked(self, phased_protocol):
        builder = GraphBuilder(phased_protocol)
        state = {
            "task": {"id": "t1", "spec": "test"},
            "current_mode": "producer",
            "iteration": 1,
            "stage": "validate",
            "validation_results": [],
            "validation_token": None,
            "artifacts": [],
            "constraints_passed": True,
            "constraint_violations": [],
            "policy_decision": None,
            "is_complete": False,
            "is_blocked": True,
            "metadata": {},
        }
        assert builder._route_after_post_validation(state) == "blocked"

    def test_end_to_end_phased_execution(self, phased_protocol, project_dir):
        """Full loop with both pre and post validators.

        Uses a custom validator_fn that returns pass for LLM-backed
        validators (security) so the unanimous policy proceeds.
        """
        def _all_pass(task, validators, shell_mcp, current_mode="", **kwargs):
            results = []
            for v in validators:
                if v.validator_type == "quality" and shell_mcp:
                    # Let quality validator run normally (echo tests pass)
                    from snodo.validators.quality import QualityValidator
                    qv = QualityValidator(validator_spec=v)
                    results.append(qv.evaluate())
                else:
                    results.append(ValidatorResult(
                        validator_id=v.validator_id, severity="pass",
                        justification="mock pass"))
            return results

        graph = build_protocol_graph(
            phased_protocol,
            project_root=project_dir,
            use_mock_coder=True,
            validator_fn=_all_pass,
        )
        compiled = graph.compile()

        initial_state = {
            "task": {"id": "t1", "spec": "test task"},
            "current_mode": "producer",
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
        assert result["stage"] == LoopStage.COMPLETE.value
        assert result["is_complete"] is True

    def test_backward_compat_protocol_without_phases(self, project_dir):
        """Old-style protocol without evaluation_phase still works."""
        protocol = Protocol(
            protocol_id="old",
            name="Old Protocol",
            modes=[
                Mode(mode_id="m1", name="M", validators=["v1"]),
            ],
            validators=[
                Validator(validator_id="v1", validator_type="security"),
            ],
            initial_mode="m1",
        )

        # All validators default to pre_execute
        assert protocol.validators[0].evaluation_phase == "pre_execute"

        graph = build_protocol_graph(
            protocol,
            project_root=project_dir,
            use_mock_coder=True,
        )
        compiled = graph.compile()

        result = compiled.invoke({
            "task": {"id": "t1", "spec": "test"},
            "current_mode": "m1",
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
        })
        # No-criteria validator now returns warn → policy escalates → blocked
        assert result["stage"] == LoopStage.BLOCKED.value


# === Loop: Quality Validator Dispatch ===

class TestQualityDispatch:
    """Tests for quality validator dispatch in _default_validator."""

    def test_quality_type_dispatches_to_quality_validator(self, project_dir):
        """validator_type: quality should use QualityValidator, not stub."""
        protocol = Protocol(
            protocol_id="test",
            name="Test",
            modes=[Mode(mode_id="m1", name="M", validators=["quality"])],
            validators=[
                Validator(
                    validator_id="quality",
                    validator_type="quality",
                    evaluation_phase="post_execute",
                    tooling={"test_command": "echo ok"},
                ),
            ],
            initial_mode="m1",
        )
        from snodo.mcp.workspace import WorkspaceMCP
        builder = GraphBuilder(
            protocol,
            workspace_mcp=WorkspaceMCP(project_dir),
        )
        from snodo.core.interfaces import Task
        task = Task(id="t1", spec="test")
        validators = [protocol.get_validator("quality")]

        with patch("snodo.validators.quality.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
            results = builder._default_validator(task, validators, None)

        assert len(results) == 1
        assert results[0].validator_id == "quality"
        assert results[0].severity == "pass"
        # Should NOT contain "Stub" — real QualityValidator was used
        assert "Stub" not in results[0].justification

    def test_quality_test_failure_is_blocker_not_warn(self, project_dir):
        """Test failures from quality validator stay as blockers."""
        protocol = Protocol(
            protocol_id="test",
            name="Test",
            modes=[Mode(mode_id="m1", name="M", validators=["quality"])],
            validators=[
                Validator(
                    validator_id="quality",
                    validator_type="quality",
                    evaluation_phase="post_execute",
                    tooling={"test_command": "pytest"},
                ),
            ],
            initial_mode="m1",
        )
        from snodo.mcp.workspace import WorkspaceMCP
        builder = GraphBuilder(
            protocol,
            workspace_mcp=WorkspaceMCP(project_dir),
        )
        from snodo.core.interfaces import Task
        task = Task(id="t1", spec="test")
        validators = [protocol.get_validator("quality")]

        with patch("snodo.validators.quality.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stdout="2 failed\n", stderr="")
            results = builder._default_validator(task, validators, None)

        assert results[0].severity == "blocker"

    def test_security_type_not_dispatched_as_quality(self, project_dir):
        """validator_type: security should NOT use QualityValidator."""
        protocol = Protocol(
            protocol_id="test",
            name="Test",
            modes=[Mode(mode_id="m1", name="M", validators=["sec"])],
            validators=[
                Validator(
                    validator_id="sec",
                    validator_type="security",
                    evaluation_phase="pre_execute",
                ),
            ],
            initial_mode="m1",
        )
        builder = GraphBuilder(protocol)
        from snodo.core.interfaces import Task
        task = Task(id="t1", spec="test")
        validators = [protocol.get_validator("sec")]

        results = builder._default_validator(task, validators, None)

        assert len(results) == 1
        assert results[0].severity == "warn"
        assert "No criteria" in results[0].justification

    def test_quality_validator_gets_workspace_root(self, project_dir):
        """QualityValidator receives workspace root via context."""
        from snodo.mcp.workspace import WorkspaceMCP
        spec = Validator(
            validator_id="quality",
            validator_type="quality",
            evaluation_phase="post_execute",
            tooling={"test_command": "echo ok"},
        )
        protocol = Protocol(
            protocol_id="test",
            name="Test",
            modes=[Mode(mode_id="m1", name="M", validators=["quality"])],
            validators=[spec],
            initial_mode="m1",
        )

        workspace = WorkspaceMCP(project_dir)
        builder = GraphBuilder(protocol, workspace_mcp=workspace)
        from snodo.core.interfaces import Task
        task = Task(id="t1", spec="test")

        # Registry dispatch uses the real class — verify through result
        with patch("snodo.validators.quality.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
            results = builder._default_validator(task, [spec], None)

        assert len(results) == 1
        assert results[0].severity == "pass"
        assert "Stub" not in results[0].justification
