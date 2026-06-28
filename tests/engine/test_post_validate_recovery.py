"""Tests for post-validate recovery (auto-fix subtask spawning, ADR 013).

Covers: recoverable vs terminal classification, subtask spawning,
depth cap recovery_exhausted, routing, and audit events.
"""

import pytest
from unittest.mock import MagicMock

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
