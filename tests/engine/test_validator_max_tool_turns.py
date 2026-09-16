"""A validator's reading budget is set per validator, not just per engine.

``max_tool_turns`` used to be one number for every validator in a protocol.
A validator spec may now declare its own budget, falling back to the
configured ``llm.validator.max_tool_turns`` when it does not — the same shape
as the existing per-validator ``model`` override (see
tests/engine/test_validator_model_override.py).
"""

from unittest.mock import MagicMock

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task
from snodo.engine.validators import ValidatorRunner


def _stub_result(vid, sev="pass"):
    from snodo.core.interfaces import ValidatorResult
    return ValidatorResult(validator_id=vid, severity=sev, justification="stub")


def _runner():
    return ValidatorRunner(
        protocol=MagicMock(), completion_fn=MagicMock(),
        default_model="claude-sonnet-4-20250514",
        validator_config=MagicMock(max_tokens=1500, max_tool_turns=50),
        audit_log=None, workspace_mcp=None, git_mcp=None, session_manager=None,
    )


class TestValidatorMaxToolTurns:
    """A validator spec's own budget overrides the configured default."""

    def test_declared_budget_is_used(self):
        runner = _runner()
        dispatched = []
        runner._dispatch_one = (
            lambda v, ctx, reg: dispatched.append((v.validator_id, ctx.max_tool_turns))
            or _stub_result(v.validator_id)
        )

        runner.run(
            Task(id="t1", spec="test"),
            [Validator(validator_id="architecture", validator_type="architecture", max_tool_turns=6)],
            None, current_mode="producer",
        )

        assert dispatched[0] == ("architecture", 6)

    def test_undeclared_budget_falls_back_to_configured(self):
        runner = _runner()
        dispatched = []
        runner._dispatch_one = (
            lambda v, ctx, reg: dispatched.append((v.validator_id, ctx.max_tool_turns))
            or _stub_result(v.validator_id)
        )

        runner.run(
            Task(id="t1", spec="test"),
            [Validator(validator_id="security", validator_type="security")],
            None, current_mode="producer",
        )

        assert dispatched[0] == ("security", 50)

    def test_each_validator_keeps_its_own_budget(self):
        runner = _runner()
        dispatched = []
        runner._dispatch_one = (
            lambda v, ctx, reg: dispatched.append((v.validator_id, ctx.max_tool_turns))
            or _stub_result(v.validator_id)
        )

        runner.run(
            Task(id="t1", spec="test"),
            [
                Validator(validator_id="architecture", validator_type="architecture", max_tool_turns=6),
                Validator(validator_id="security", validator_type="security"),
            ],
            None, current_mode="producer",
        )

        by_id = dict(dispatched)
        assert by_id["architecture"] == 6
        assert by_id["security"] == 50
