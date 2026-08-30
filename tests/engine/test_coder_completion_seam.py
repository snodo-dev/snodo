"""Tests proving validator/classifier completion_fn decoupling from coder object (Fixes #143).

Covers:
1. Coders with no _completion_fn (e.g. opencode, external agents) get working validators & classifier from config.
2. The litellm path behavior is unchanged (same client function rebound via _build_completion_fn).
3. Per-validator `model:` overrides still take precedence.
4. With no usable validator client at all, validation halts fail-closed (blocker with error=True).
"""

from unittest.mock import MagicMock, patch

from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.coders.base import Coder
from snodo.engine.loop import GraphBuilder
from snodo.engine.policy import PolicyEvaluator, PolicyAction
from snodo.validators.llm_validator import LLMValidator


class CustomNoCompletionCoder(Coder):
    """Custom coder adapter with no _completion_fn or completion_fn attribute."""

    def __init__(self, model: str = "custom-coder-binary"):
        self.model = model

    def implement(self, task: Task, context: dict):
        return []

    def execute(self, task: Task, context: dict):
        return []


def _make_protocol(validator_model: str = None) -> Protocol:
    v = Validator(
        validator_id="quality_llm",
        validator_type="quality",
        criteria=["Ensure code quality"],
        model=validator_model,
    )
    m = Mode(
        mode_id="producer",
        name="Producer",
        tools=["edit"],
        validators=["quality_llm"],
    )
    return Protocol(
        protocol_id="test_p",
        name="Test Protocol",
        version="1.0.0",
        initial_mode="producer",
        modes=[m],
        validators=[v],
        disagreement_policy="unanimous",
    )


def test_coder_with_no_completion_fn_gets_working_validator_and_classifier(tmp_path, monkeypatch):
    """A coder with no _completion_fn still gets working validator and classifier completion functions."""
    coder = CustomNoCompletionCoder()
    assert not hasattr(coder, "_completion_fn") or coder._completion_fn is None

    protocol = _make_protocol()

    with patch("snodo.config.ConfigManager.load", return_value={"model": "gpt-4o"}):
        builder = GraphBuilder(
            protocol=protocol,
            coder=coder,
        )

    # Builder constructs validator runner and classifier completion_fn from config
    assert builder._validator_runner._completion_fn is not None
    assert builder._classifier_completion_fn is not None

    # Verify completion function points to litellm_completion bound to model
    from litellm import completion as litellm_completion
    assert builder._validator_runner._completion_fn.func == litellm_completion
    assert builder._classifier_completion_fn.func == litellm_completion


def test_litellm_path_is_unchanged():
    """The litellm path rebinds the coder's own completion function."""
    from snodo.coders.litellm import LiteLLMAdapter

    custom_completion = MagicMock()
    coder = LiteLLMAdapter(model="claude-sonnet-4-20250514")
    coder._completion_fn = custom_completion
    protocol = _make_protocol()

    with patch("snodo.config.ConfigManager.load", return_value={"model": "claude-sonnet-4-20250514"}):
        builder = GraphBuilder(
            protocol=protocol,
            coder=coder,
        )

    # The coder's own completion_fn is used as base_fn
    assert builder._completion_fn == custom_completion
    assert builder._validator_runner._completion_fn.func == custom_completion
    assert builder._classifier_completion_fn.func == custom_completion


def test_per_validator_model_override_still_wins():
    """A per-validator `model:` override in protocol.yml takes precedence over default validator model."""
    coder = CustomNoCompletionCoder()
    protocol = _make_protocol(validator_model="gemini-2.0-flash")

    with patch("snodo.config.ConfigManager.load", return_value={"model": "claude-sonnet-4-20250514"}):
        builder = GraphBuilder(
            protocol=protocol,
            coder=coder,
        )

    dispatched = []
    builder._validator_runner._dispatch_one = (
        lambda v, ctx, reg: dispatched.append((v.validator_id, ctx.model)) or ValidatorResult(validator_id=v.validator_id, severity="pass", justification="ok")
    )

    results = builder._validator_runner.run(
        Task(id="t1", spec="test spec"),
        protocol.validators,
        None,
        current_mode="producer",
    )

    assert len(results) == 1
    assert len(dispatched) == 1
    # per-validator model override "gemini-2.0-flash" won over default "claude-sonnet-4-20250514"
    assert dispatched[0] == ("quality_llm", "gemini-2.0-flash")


def test_no_usable_validator_client_halts_fail_closed():
    """When completion_fn is None, LLM validator produces a fail-closed blocker with error=True."""
    from snodo.compiler.models import DisagreementPolicy

    v_spec = Validator(
        validator_id="quality_llm",
        validator_type="quality",
        criteria=["Check code"],
    )
    val = LLMValidator(validator_spec=v_spec, completion_fn=None)
    context = MagicMock()
    context.completion_fn = None

    result = val.evaluate(context)

    # Must be severity="blocker" and error=True
    assert result.severity == "blocker"
    assert result.error is True
    assert "No completion_fn available" in result.justification

    # Policy evaluator must HALT fail-closed
    evaluator = PolicyEvaluator()
    policy_decision = evaluator.evaluate([result], DisagreementPolicy.UNANIMOUS, "pre_execute")
    assert policy_decision.action == PolicyAction.HALT
    assert policy_decision.blocker_count == 1
