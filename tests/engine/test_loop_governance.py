"""Governance node branch coverage tests.

FILE: tests/engine/test_loop_governance.py
"""

from unittest.mock import MagicMock, patch

import pytest
from snodo.compiler.models import Protocol
from snodo.core.interfaces import Task
from snodo.engine.loop import GraphBuilder


@pytest.fixture
def sample_protocol():
    from snodo.compiler.models import Mode, Validator
    return Protocol(
        protocol_id="test_protocol",
        name="Test Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer Mode",
                tools=["edit", "test"],
                validators=["security"]
            )
        ],
        validators=[
            Validator(
                validator_id="security",
                validator_type="security",
                criteria=["Check OWASP Top 10"]
            )
        ],
        initial_mode="producer"
    )


@pytest.fixture
def sample_task():
    return Task(
        id="task_001",
        spec="Implement feature X"
    )


def test_governance_max_iterations_exceeded(sample_protocol, sample_task):
    """Test that iteration > 50 blocks the loop with max_iterations halt type."""
    builder = GraphBuilder(sample_protocol)

    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 50,
        "stage": "governance",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {}
    }

    result = builder._governance_node(initial_state)
    assert result["is_blocked"] is True
    assert result["halt_type"] == "max_iterations"
    assert "Max iterations (50) exceeded" in result["constraint_violations"][0]


def test_governance_wave_classification_failure(sample_protocol, sample_task, capsys):
    """Test wave-classification exception path printing '[WAVE] classification failed'."""
    builder = GraphBuilder(sample_protocol)
    builder._project_root = "/fake/project"

    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
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
        "metadata": {}
    }

    with patch("snodo.infrastructure.wave_registry.WaveRegistry.classify_task", side_effect=Exception("mocked classification error")):
        result = builder._governance_node(initial_state)

    captured = capsys.readouterr()
    assert "[WAVE] classification failed for task_001: mocked classification error" in captured.err
    # The flow type and wave_id should remain unchanged (None) on error
    assert result["task"]["flow_type"] is None
    assert result["task"]["wave_id"] is None


def test_classify_wave_uses_single_resolved_model(sample_protocol, sample_task):
    """The classification call passes the once-resolved classifier model.

    The model bound to the completion function and the model passed to the
    call must be the same value, so model and api_base can never disagree
    (ADR 020).
    """
    cf_model = "openai/@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    ds_model = "deepseek/deepseek-v4-flash"
    cf_api_base = "https://api.cloudflare.com/client/v4"

    fake_config = {
        "model": ds_model,
        "llm": {
            "classifier": {"model": cf_model},
            "validator": {"model": ds_model},
        },
    }

    def _fake_resolve_api_base(model):
        return cf_api_base if model == cf_model else None

    with (
        patch("snodo.config.ConfigManager.load", return_value=fake_config),
        patch("snodo.config.ConfigManager.resolve_api_base", side_effect=_fake_resolve_api_base),
    ):
        builder = GraphBuilder(sample_protocol)

    # The single resolved model is stored and matches the bound completion fn.
    assert builder._classifier_model == cf_model
    cc_keywords = getattr(builder._classifier_completion_fn, "keywords", {})
    assert cc_keywords.get("model") == cf_model
    assert cc_keywords.get("api_base") == cf_api_base

    # The classification call passes the same model, not a re-resolved one.
    builder._project_root = "/fake/project"
    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
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
    with patch(
        "snodo.infrastructure.wave_registry.WaveRegistry.classify_task",
        return_value={"flow_type": "feature", "wave_id": "w_0001", "task_summary": "s"},
    ) as mock_classify:
        builder._governance_node(state)

    call_model = mock_classify.call_args[0][3]
    assert call_model == cf_model
    assert call_model == cc_keywords.get("model")


def test_classify_wave_passes_classifier_config(sample_protocol, sample_task):
    """WaveRegistry is constructed with the classifier config (budget/temperature)."""
    builder = GraphBuilder(sample_protocol)
    builder._project_root = "/fake/project"

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
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
    with patch("snodo.infrastructure.wave_registry.WaveRegistry") as mock_registry_cls:
        mock_registry = mock_registry_cls.return_value
        mock_registry.classify_task.return_value = {
            "flow_type": "feature", "wave_id": "w_0001", "task_summary": "s",
        }
        builder._governance_node(state)

    # The registry was constructed with a classifier= kwarg.
    assert "classifier" in mock_registry_cls.call_args[1]


def test_classify_wave_emits_audit_event(sample_protocol, sample_task):
    """The classification is emitted into the audit trail (Fixes #154).

    cloud_sync ships audit events only, so wave_id must reach an audit event
    for a consumer of the event stream to see which wave a task belongs to.
    flow_type belongs with it. An unwaved task (wave_id None) is legitimate
    and is emitted as an empty wave_id — distinguishable from a failed
    classification, which raises before the event is appended.
    """
    audit = MagicMock()
    builder = GraphBuilder(sample_protocol, audit_log=audit)
    builder._project_root = "/fake/project"

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
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
    with patch(
        "snodo.infrastructure.wave_registry.WaveRegistry.classify_task",
        return_value={"flow_type": "defect", "wave_id": "w_0001", "task_summary": "s"},
    ):
        builder._governance_node(state)

    audit.append_event.assert_any_call("task_classified", {
        "op": "task_classified",
        "task_ref": "task_001",
        "flow_type": "defect",
        "wave_id": "w_0001",
        "task_summary": "s",
    })


def test_classify_wave_audit_event_unwaved(sample_protocol, sample_task):
    """An unwaved task is emitted with an empty wave_id, not fabricated."""
    audit = MagicMock()
    builder = GraphBuilder(sample_protocol, audit_log=audit)
    builder._project_root = "/fake/project"

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
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
    with patch(
        "snodo.infrastructure.wave_registry.WaveRegistry.classify_task",
        return_value={"flow_type": "feature", "wave_id": None, "task_summary": None},
    ):
        builder._governance_node(state)

    audit.append_event.assert_any_call("task_classified", {
        "op": "task_classified",
        "task_ref": "task_001",
        "flow_type": "feature",
        "wave_id": "",
        "task_summary": None,
    })


def test_governance_node_delegates_to_single_classify_wave(sample_protocol, sample_task):
    """loop.py's _governance_node no longer inlines classification — it calls
    the single _classify_wave path (ADR 020)."""
    builder = GraphBuilder(sample_protocol)
    builder._project_root = "/fake/project"

    state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
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
    with patch.object(builder, "_classify_wave") as mock_cw:
        builder._governance_node(state)
    mock_cw.assert_called_once()
