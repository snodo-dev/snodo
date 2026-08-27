"""Tests for post-validate recovery (auto-fix subtask spawning, ADR 013).

Covers: recoverable vs terminal classification, subtask spawning,
depth cap recovery_exhausted, routing, and audit events.
"""

import pytest
from unittest.mock import MagicMock, patch

from snodo.compiler.models import Protocol, Mode, Validator, DisagreementPolicy, ExecutionConfig
from snodo.engine.loop import GraphBuilder
from snodo.core.interfaces import ValidatorResult


@pytest.fixture
def base_protocol():
    return Protocol(
        protocol_id="test_proto",
        name="Test",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=["quality"],
            )
        ],
        validators=[
            Validator(
                validator_id="quality",
                validator_type="quality",
                evaluation_phase="post_execute",
                criteria=["check outputs"],
                severity_cap="warn",
            )
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )


def _make_state(task_id="t1", depth=0, **overrides):
    state = {
        "task": {"id": task_id, "spec": "test spec", "depth": depth},
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
        "halt_type": None,
        "pending_disagreement": None,
        "metadata": {},
        "messages": [],
        "summary": "",
        "spawned_subtasks": [],
        "needs_recovery": False,
    }
    state.update(overrides)
    return state


class TestRecoverableClassification:
    """Verify TERMINAL vs RECOVERABLE classification."""

    def test_recoverable_blocker_spawns_subtask(self, base_protocol):
        """HALT (non-error, overridable) → spawned_subtasks[0] with correct parent/depth."""
        def _blocker_validator(task, validators, shell_mcp, **kwargs):
            return [ValidatorResult(validator_id="quality", severity="blocker",
                                    justification="Code quality too low")]
        builder = GraphBuilder(base_protocol, validator_fn=_blocker_validator)
        result = builder._post_validate_node(_make_state())
        assert result["needs_recovery"] is True
        assert result["is_blocked"] is False
        assert len(result["spawned_subtasks"]) == 1
        sub = result["spawned_subtasks"][0]
        assert sub["parent_task_ref"] == "t1"
        assert sub["depth"] == 1

    def test_validator_error_halts(self, base_protocol):
        """HALT with error → is_blocked True, halt_type validator_error."""
        class ErrorResult:
            validator_id = "quality"
            severity = "blocker"
            justification = "error"
            error = True
            def model_dump(self):
                return {"validator_id": "quality", "severity": "blocker", "justification": "error"}

        def _error_validator(task, validators, shell_mcp, **kwargs):
            return [ErrorResult()]

        builder = GraphBuilder(base_protocol, validator_fn=_error_validator)
        result = builder._post_validate_node(_make_state())
        assert result["is_blocked"] is True
        assert result["halt_type"] == "validator_error"
        assert len(result["spawned_subtasks"]) == 0
        assert result["needs_recovery"] is False

    def test_escalate_spawns_subtask(self):
        """ESCALATE → needs_recovery, subtask spawned."""
        protocol = Protocol(
            protocol_id="test", name="Test", version="1.0.0",
            modes=[Mode(mode_id="producer", name="Producer", tools=["edit"], validators=["v1"])],
            validators=[Validator(validator_id="v1", validator_type="test", evaluation_phase="post_execute", criteria=["x"])],
            disagreement_policy=DisagreementPolicy.UNANIMOUS,
            initial_mode="producer",
        )

        def _warn_validator(task, validators, shell_mcp, **kwargs):
            return [ValidatorResult(validator_id="v1", severity="warn",
                                    justification="needs review")]
        builder = GraphBuilder(protocol, validator_fn=_warn_validator)
        result = builder._post_validate_node(_make_state(task_id="t2"))
        assert result["needs_recovery"] is True
        assert result["is_blocked"] is False
        assert len(result["spawned_subtasks"]) == 1
        assert result["spawned_subtasks"][0]["parent_task_ref"] == "t2"

    def test_non_overridable_blocker_halts(self):
        """Blocker from validator WITHOUT severity_cap → is_blocked True."""
        protocol = Protocol(
            protocol_id="test", name="Test", version="1.0.0",
            modes=[Mode(mode_id="producer", name="Producer", tools=["edit"], validators=["v1"])],
            validators=[Validator(validator_id="v1", validator_type="test", evaluation_phase="post_execute", criteria=["x"])],
            disagreement_policy=DisagreementPolicy.UNANIMOUS,
            initial_mode="producer",
        )

        def _blocker(task, validators, shell_mcp, **kwargs):
            return [ValidatorResult(validator_id="v1", severity="blocker",
                                    justification="structural issue")]
        builder = GraphBuilder(protocol, validator_fn=_blocker)
        result = builder._post_validate_node(_make_state(task_id="t3"))
        assert result["is_blocked"] is True
        assert result["halt_type"] == "blocked"
        assert result["needs_recovery"] is False
        assert len(result["spawned_subtasks"]) == 0


class TestRecoveryDepthCap:
    """Verify depth cap behavior."""

    def test_default_max_recovery_depth_is_three(self):
        """Default max_recovery_depth is 3 (Fixes #40)."""
        cfg = ExecutionConfig()
        assert cfg.max_recovery_depth == 3

    def test_recovery_exhausted_at_depth_cap(self):
        """Task at max_recovery_depth → is_blocked, halt_type recovery_exhausted."""
        protocol = Protocol(
            protocol_id="test", name="Test", version="1.0.0",
            modes=[Mode(mode_id="producer", name="Producer", tools=["edit"], validators=["v1"])],
            validators=[Validator(validator_id="v1", validator_type="test", evaluation_phase="post_execute", criteria=["x"],
                                   severity_cap="warn")],
            disagreement_policy=DisagreementPolicy.UNANIMOUS,
            initial_mode="producer",
            execution=ExecutionConfig(max_recovery_depth=2),
        )

        def _blocker(task, validators, shell_mcp, **kwargs):
            return [ValidatorResult(validator_id="v1", severity="blocker",
                                    justification="fix needed")]
        builder = GraphBuilder(protocol, validator_fn=_blocker)
        # depth=2 is at the cap (max=2), so should be exhausted
        result = builder._post_validate_node(_make_state(task_id="t_deep", depth=2))
        assert result["is_blocked"] is True
        assert result["halt_type"] == "recovery_exhausted"
        assert len(result["spawned_subtasks"]) == 0
        assert result["needs_recovery"] is False

    def test_recovery_within_depth_budget(self):
        """Task within max_recovery_depth → subtask spawned."""
        protocol = Protocol(
            protocol_id="test", name="Test", version="1.0.0",
            modes=[Mode(mode_id="producer", name="Producer", tools=["edit"], validators=["v1"])],
            validators=[Validator(validator_id="v1", validator_type="test", evaluation_phase="post_execute", criteria=["x"],
                                   severity_cap="warn")],
            disagreement_policy=DisagreementPolicy.UNANIMOUS,
            initial_mode="producer",
            execution=ExecutionConfig(max_recovery_depth=3),
        )

        def _blocker(task, validators, shell_mcp, **kwargs):
            return [ValidatorResult(validator_id="v1", severity="blocker",
                                    justification="fix me")]
        builder = GraphBuilder(protocol, validator_fn=_blocker)
        # depth=2 < max=3 → subtask at depth=3
        result = builder._post_validate_node(_make_state(task_id="t_mid", depth=2))
        assert result["is_blocked"] is False
        assert result["needs_recovery"] is True
        assert len(result["spawned_subtasks"]) == 1
        assert result["spawned_subtasks"][0]["depth"] == 3
        assert result["spawned_subtasks"][0]["parent_task_ref"] == "t_mid"

    def test_recovery_exhausted_audit(self):
        """recovery_exhausted audit event emitted at cap."""
        protocol = Protocol(
            protocol_id="test", name="Test", version="1.0.0",
            modes=[Mode(mode_id="producer", name="Producer", tools=["edit"], validators=["v1"])],
            validators=[Validator(validator_id="v1", validator_type="test", evaluation_phase="post_execute", criteria=["x"],
                                   severity_cap="warn")],
            disagreement_policy=DisagreementPolicy.UNANIMOUS,
            initial_mode="producer",
            execution=ExecutionConfig(max_recovery_depth=1),
        )

        def _blocker(task, validators, shell_mcp, **kwargs):
            return [ValidatorResult(validator_id="v1", severity="blocker",
                                    justification="fix needed")]
        mock_audit = MagicMock()
        builder = GraphBuilder(protocol, validator_fn=_blocker, audit_log=mock_audit)
        builder._post_validate_node(_make_state(task_id="t_aud", depth=1))
        mock_audit.append_event.assert_any_call("recovery_exhausted", {
            "op": "recovery_exhausted",
            "task_ref": "t_aud",
            "depth": 1,
            "max_depth": 1,
        })


class TestSubtaskSpawnedAudit:
    """Verify subtask_spawned audit event."""

    def test_subtask_spawned_audit_emitted(self, base_protocol):
        def _blocker(task, validators, shell_mcp, **kwargs):
            return [ValidatorResult(validator_id="quality", severity="blocker",
                                    justification="too low")]
        mock_audit = MagicMock()
        builder = GraphBuilder(base_protocol, validator_fn=_blocker, audit_log=mock_audit)
        builder._post_validate_node(_make_state(task_id="t_audit"))
        mock_audit.append_event.assert_any_call("subtask_spawned", {
            "op": "subtask_spawned",
            "parent_ref": "t_audit",
            "task_ref": "t_audit_fix_1",
            "depth": 1,
            "triggering_validator_ids": ["quality"],
        })


class TestRouteAfterPostValidation:
    """Verify routing decisions."""

    def test_route_recovery(self, base_protocol):
        builder = GraphBuilder(base_protocol)
        state = _make_state(needs_recovery=True)
        assert builder._route_after_post_validation(state) == "recovery"

    def test_route_blocked(self, base_protocol):
        builder = GraphBuilder(base_protocol)
        state = _make_state(is_blocked=True)
        assert builder._route_after_post_validation(state) == "blocked"

    def test_route_move_next(self, base_protocol):
        builder = GraphBuilder(base_protocol)
        state = _make_state()
        assert builder._route_after_post_validation(state) == "move_next"


class TestRecoverySpec:
    """The recovery spec must be an instruction, not a truncated report."""

    def _failures(self, *entries):
        """Build failure-entry dicts (as _build_recovery_spec consumes them)."""
        return [dict(e) for e in entries]

    def test_spec_is_instruction_not_state_description(self, base_protocol):
        from snodo.engine.loop import _build_recovery_spec

        failures = self._failures(
            {"attempt": 1, "validator_id": "quality", "severity": "blocker",
             "justification": "The tree contains src/scripts/vcard.js"},
        )
        spec = _build_recovery_spec("implement vcard export", failures)

        assert "INTENT" in spec
        assert "CONSTRAINTS" in spec
        assert "FAILURES" in spec
        assert "implement vcard export" in spec
        # The justification is preserved verbatim as context, not truncated.
        assert "The tree contains src/scripts/vcard.js" in spec
        # It reads as an instruction, not a bare state description.
        assert "quality" in spec

    def test_intent_is_the_operative_instruction(self, base_protocol):
        """The intent is the task; the failures are diagnostic evidence, not a
        second mandate (Fixes #78)."""
        from snodo.engine.loop import _build_recovery_spec

        failures = self._failures(
            {"attempt": 1, "validator_id": "quality", "severity": "blocker",
             "justification": "Tests failed (exit 2). Output:\nAssertionError: x"},
        )
        spec = _build_recovery_spec("implement vcard export", failures)

        # The operative instruction is the intent, stated first.
        assert spec.index("The task is the INTENT below") < spec.index("FAILURES")
        # The failures are explicitly framed as evidence, not as the task.
        assert "diagnostic evidence" in spec
        assert "do not change the task" in spec
        assert "do not widen its scope" in spec
        # The old framing that made the failure list the mandate is gone.
        assert "Fix the following failures" not in spec
        assert "Address every failure listed below" not in spec

    def test_failures_do_not_widen_scope(self, base_protocol):
        """A recovery spec must not invite exploration beyond the intent, no
        matter how many failures accumulate (Fixes #78)."""
        from snodo.engine.loop import _build_recovery_spec

        many_failures = self._failures(*[
            {"attempt": i, "validator_id": "quality", "severity": "blocker",
             "justification": f"failure {i}"}
            for i in range(1, 5)
        ])
        spec = _build_recovery_spec("implement vcard export", many_failures)

        # The scope anchor is the intent; the failures are evidence attached
        # to it, never a widening instruction.
        assert "Implement exactly the intent above" in spec
        assert "Do not expand the task beyond it" in spec
        assert "they do not change the task" in spec
        assert "do not widen its scope" in spec

    def test_spec_never_truncates_justification(self, base_protocol):
        from snodo.engine.loop import _build_recovery_spec

        long_justification = "A" * 600
        failures = self._failures(
            {"attempt": 1, "validator_id": "quality", "severity": "warn",
             "justification": long_justification},
        )
        spec = _build_recovery_spec("do the thing", failures)

        # The full justification survives — no mid-sentence cut.
        assert long_justification in spec

    def test_original_intent_appears_exactly_once(self, base_protocol):
        """The original intent is carried once, unchanged, never wrapped."""
        from snodo.engine.loop import _build_recovery_spec

        intent = "implement vcard export"
        # A prior recovery spec already carries the wrapper text; feeding it in
        # as a failure must not duplicate the intent.
        failures = self._failures(
            {"attempt": 2, "validator_id": "quality", "severity": "blocker",
             "justification": "still failing"},
        )
        spec = _build_recovery_spec(intent, failures)
        assert spec.count(intent) == 1

    def test_failures_accumulate_across_attempts(self, base_protocol):
        """Two failures from two attempts both appear, attributed by attempt."""
        from snodo.engine.loop import _build_recovery_spec

        failures = self._failures(
            {"attempt": 1, "validator_id": "quality", "severity": "blocker",
             "justification": "Tests failed (exit 2). Output:\nAssertionError: x"},
            {"attempt": 2, "validator_id": "quality", "severity": "blocker",
             "justification": "Tests failed (exit 2). Output:\nTypeError: y"},
        )
        spec = _build_recovery_spec("do the thing", failures)

        assert "[attempt 1]" in spec
        assert "[attempt 2]" in spec
        assert "AssertionError: x" in spec
        assert "TypeError: y" in spec

    def test_spawned_subtask_uses_synthesised_spec(self, base_protocol):
        def _blocker(task, validators, shell_mcp, **kwargs):
            return [ValidatorResult(validator_id="quality", severity="blocker",
                                    justification="Code quality too low")]
        builder = GraphBuilder(base_protocol, validator_fn=_blocker)
        state = _make_state()
        state["task"]["spec"] = "original spec"
        result = builder._post_validate_node(state)
        sub = result["spawned_subtasks"][0]
        assert "The task is the INTENT below" in sub["spec"]
        assert "original spec" in sub["spec"]
        assert "Code quality too low" in sub["spec"]
        # No bare "Fix post-validation issues: ..." prefix.
        assert not sub["spec"].startswith("Fix post-validation issues:")


class TestRecoveryLinearIds:
    """Recovery ids are numbered linearly off the root, never nested."""

    def _spawn(self, depth, task_id="task_X", max_depth=3):
        protocol = Protocol(
            protocol_id="test", name="Test", version="1.0.0",
            modes=[Mode(mode_id="producer", name="Producer", tools=["edit"],
                         validators=["v1"])],
            validators=[Validator(validator_id="v1", validator_type="test",
                                  evaluation_phase="post_execute", criteria=["x"],
                                  severity_cap="warn")],
            disagreement_policy=DisagreementPolicy.UNANIMOUS,
            initial_mode="producer",
            execution=ExecutionConfig(max_recovery_depth=max_depth),
        )

        def _blocker(task, validators, shell_mcp, **kwargs):
            return [ValidatorResult(validator_id="v1", severity="blocker",
                                    justification="fix me")]

        builder = GraphBuilder(protocol, validator_fn=_blocker)
        state = _make_state(task_id=task_id, depth=depth)
        state["task"]["spec"] = "original task"
        state["task"]["root_task_ref"] = task_id
        state["task"]["root_spec"] = "implement the vcard export"
        state["task"]["prior_failures"] = [
            {"attempt": d, "validator_id": "v1", "severity": "blocker",
             "justification": f"failure at attempt {d}"}
            for d in range(1, depth + 1)
        ]
        return builder._post_validate_node(state)

    def test_depth_2_spawns_fix_3(self):
        """At depth 2, the spawned subtask is task_X_fix_3, not nested."""
        result = self._spawn(depth=2)
        sub = result["spawned_subtasks"][0]
        assert sub["id"] == "task_X_fix_3"
        assert sub["depth"] == 3

    def test_depth_3_spec_has_original_intent_once(self):
        """A depth-3 recovery spec contains the original intent exactly once."""
        result = self._spawn(depth=2)
        sub = result["spawned_subtasks"][0]
        assert sub["spec"].count("implement the vcard export") == 1


class TestRecoveryStalled:
    """An identical repeated verdict halts recovery before depth is exhausted."""

    def test_repeated_verdict_stalls(self):
        protocol = Protocol(
            protocol_id="test", name="Test", version="1.0.0",
            modes=[Mode(mode_id="producer", name="Producer", tools=["edit"],
                         validators=["v1"])],
            validators=[Validator(validator_id="v1", validator_type="test",
                                  evaluation_phase="post_execute", criteria=["x"],
                                  severity_cap="warn")],
            disagreement_policy=DisagreementPolicy.UNANIMOUS,
            initial_mode="producer",
            execution=ExecutionConfig(max_recovery_depth=3),
        )

        def _blocker(task, validators, shell_mcp, **kwargs):
            return [ValidatorResult(validator_id="v1", severity="blocker",
                                    justification="same failure")]

        builder = GraphBuilder(protocol, validator_fn=_blocker)
        # depth=1 with a prior failure at attempt 1 identical to what _blocker
        # will produce now (attempt 2), so the loop must stall before spawning.
        state = _make_state(task_id="task_X", depth=1)
        state["task"]["spec"] = "original task"
        state["task"]["root_task_ref"] = "task_X"
        state["task"]["root_spec"] = "original task"
        state["task"]["prior_failures"] = [
            {"attempt": 1, "validator_id": "v1", "severity": "blocker",
             "justification": "same failure"},
        ]
        result = builder._post_validate_node(state)

        assert result["is_blocked"] is True
        assert result["halt_type"] == "recovery_stalled"
        assert len(result["spawned_subtasks"]) == 0

    def test_distinct_verdict_does_not_stall(self):
        protocol = Protocol(
            protocol_id="test", name="Test", version="1.0.0",
            modes=[Mode(mode_id="producer", name="Producer", tools=["edit"],
                         validators=["v1"])],
            validators=[Validator(validator_id="v1", validator_type="test",
                                  evaluation_phase="post_execute", criteria=["x"],
                                  severity_cap="warn")],
            disagreement_policy=DisagreementPolicy.UNANIMOUS,
            initial_mode="producer",
            execution=ExecutionConfig(max_recovery_depth=3),
        )

        def _blocker(task, validators, shell_mcp, **kwargs):
            return [ValidatorResult(validator_id="v1", severity="blocker",
                                    justification="a NEW failure")]

        builder = GraphBuilder(protocol, validator_fn=_blocker)
        state = _make_state(task_id="task_X", depth=1)
        state["task"]["spec"] = "original task"
        state["task"]["root_task_ref"] = "task_X"
        state["task"]["root_spec"] = "original task"
        state["task"]["prior_failures"] = [
            {"attempt": 1, "validator_id": "v1", "severity": "blocker",
             "justification": "old different failure"},
        ]
        result = builder._post_validate_node(state)

        assert result["is_blocked"] is False
        assert result["needs_recovery"] is True
        assert len(result["spawned_subtasks"]) == 1


class TestEvidenceReachesFixTask:
    """The evidence a validator captured (the command output tail) must reach
    the fix task verbatim, not as a one-line summary."""

    def test_captured_output_reaches_fix_task_spec(self, base_protocol):
        # The quality validator now emits the full output tail.  Simulate the
        # exact shape it produces: a genuine non-zero exit with a captured tail.
        captured = (
            "E   AssertionError: assert 3 == 4\n"
            "E     +  where 3 = len(card.photo)"
        )
        justification = f"Tests failed (exit 2). Output:\n{captured}"

        def _blocker(task, validators, shell_mcp, **kwargs):
            return [ValidatorResult(validator_id="quality", severity="blocker",
                                    justification=justification)]

        builder = GraphBuilder(base_protocol, validator_fn=_blocker)
        state = _make_state()
        state["task"]["spec"] = "implement vcard export"
        result = builder._post_validate_node(state)
        sub = result["spawned_subtasks"][0]

        # The captured assertion text is present, not just a summary.
        assert "assert 3 == 4" in sub["spec"]
        assert "len(card.photo)" in sub["spec"]

    def test_quality_validator_emits_output_tail(self):
        """The quality validator's blocker justification carries the full tail,
        not a one-line summary."""
        from snodo.validators.quality import QualityValidator
        from snodo.compiler.models import Validator as V

        spec = V(validator_id="quality", validator_type="quality",
                 evaluation_phase="post_execute",
                 tooling={"test_command": "pytest"})
        qv = QualityValidator(spec, working_directory="/tmp")

        mock_result = MagicMock(
            returncode=1,
            stdout="tests/test_card.py::test_photo FAILED\nE   AssertionError: assert 3 == 4\n",
            stderr="",
        )
        with patch("snodo.validators.quality.subprocess.run", return_value=mock_result):
            result = qv.evaluate()

        assert result.severity == "blocker"
        assert "AssertionError: assert 3 == 4" in result.justification
        assert "test_photo FAILED" in result.justification
