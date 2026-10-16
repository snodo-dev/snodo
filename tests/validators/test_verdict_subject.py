"""Live submit_verdict subject binding, across judge types and phases."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task
from snodo.engine.loop import GraphBuilder
from snodo.engine.state import _build_audit_results
from snodo.validators.acceptance import AcceptanceValidator
from snodo.validators.context import ValidatorContext
from snodo.validators.llm_validator import LLMValidator
from snodo.validators.runner import run_validators

_MISSING = object()


def _completion(subject, severity="pass", missing_access=False):
    arguments = {"severity": severity, "justification": "criterion 1 checked"}
    if subject is not _MISSING:
        arguments["task_id"] = subject
    if missing_access:
        arguments["tool_access_missing"] = {"criterion": "criterion 1", "capability": "runtime"}
    tc = SimpleNamespace(id="verdict", function=SimpleNamespace(
        name="submit_verdict", arguments=json.dumps(arguments),
    ))
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
        content=None, tool_calls=[tc],
    ))])
    return MagicMock(return_value=response)


def _spec(kind="security", phase="pre_execute"):
    return Validator(
        validator_id="judge", validator_type=kind, evaluation_phase=phase,
        tools=["read_file"], criteria=["criterion 1"], severity_cap="warn",
        check_tool_access=True,
    )


@pytest.mark.parametrize("phase", ["pre_execute", "post_execute"])
@pytest.mark.parametrize("judge", [LLMValidator, AcceptanceValidator])
@pytest.mark.parametrize("subject", [_MISSING, "other-plan/proposal", "TASK-951", " task-951", None, 951])
def test_unbound_verdict_is_terminal_error(phase, judge, subject):
    completion = _completion(subject, missing_access=True)
    context = ValidatorContext(
        task=Task(id="task-951", spec="Judge this proposal"),
        task_id="stale-metadata", completion_fn=completion, model="gpt-4",
        phase=phase, workspace_mcp=MagicMock(), max_tool_turns=5,
    )
    validator = judge(_spec("acceptance" if judge is AcceptanceValidator else "security", phase))
    result = validator.evaluate(context)
    assert result.error is True
    assert result.severity == "blocker"
    assert result.validator_id == "judge"
    assert "Unusable submit_verdict" in result.justification
    assert "task-951" in result.justification
    assert result.tool_access_missing is None
    assert result.cited_criteria is None
    completion.assert_called_once()
    prompt = completion.call_args.kwargs["messages"][0]["content"]
    assert 'identifier under judgement is "task-951"' in prompt
    schema = next(t for t in completion.call_args.kwargs["tools"]
                  if t["function"]["name"] == "submit_verdict")["function"]["parameters"]
    assert "task_id" in schema["required"]
    assert schema["properties"]["task_id"]["type"] == "string"


@pytest.mark.parametrize("phase", ["pre_execute", "post_execute"])
@pytest.mark.parametrize("judge", [LLMValidator, AcceptanceValidator])
@pytest.mark.parametrize("severity", ["pass", "warn", "blocker"])
def test_matching_subject_preserves_verdict(phase, judge, severity):
    completion = _completion("task-951", severity)
    context = ValidatorContext(
        task=Task(id="task-951", spec="Judge this proposal"),
        completion_fn=completion, model="gpt-4", phase=phase,
        workspace_mcp=MagicMock(),
    )
    result = judge(_spec("acceptance" if judge is AcceptanceValidator else "security", phase)).evaluate(context)
    assert result.error is False
    assert result.severity == severity
    assert "criterion 1 checked" in result.justification
    completion.assert_called_once()


@pytest.mark.parametrize("phase", ["pre_execute", "post_execute"])
@pytest.mark.parametrize("depth", [0, 1])
@pytest.mark.parametrize("subject", [_MISSING, "another-task", "task-951"])
def test_runner_preserves_error_in_payload_audit_and_callback(phase, depth, subject):
    spec = _spec(phase=phase)
    completion = _completion(subject, missing_access=subject != "task-951")
    protocol = MagicMock()
    protocol.get_mode.return_value = MagicMock(tools=[], transitions={}, validators=["judge"])
    seen = []
    results, caps = run_validators(
        protocol=protocol, validators=[spec],
        task=Task(id="task-951", spec="proposal", depth=depth),
        phase=phase, completion_fn=completion, default_model="gpt-4",
        current_mode="build", workspace_mcp=MagicMock(),
        validator_config=SimpleNamespace(max_tokens=1500, max_tool_turns=5),
        verdict_cb=lambda vid, result: seen.append((vid, result)),
    )
    result = results[0]
    assert caps == {}
    assert seen == [("judge", result)]
    assert result.model_dump()["error"] is (subject != "task-951")
    audit = _build_audit_results([spec], results, caps)[0]
    assert audit["validator_id"] == "judge"
    assert audit["justification"] == result.justification
    if subject == "task-951":
        assert result.severity == "pass"
    else:
        assert result.error is True
        assert result.severity == "blocker"
        assert "Unusable submit_verdict" in audit["justification"]
    completion.assert_called_once()


@pytest.mark.parametrize("subject", [_MISSING, "another-plan/proposal", "task-951"])
def test_pre_execute_gate_never_dispatches_unbound_verdict(subject, tmp_path):
    from snodo.compiler.models import Mode, Protocol
    from snodo.coders.mock import MockAdapter

    spec = _spec()
    protocol = Protocol(
        protocol_id="subject-test", name="Subject test", version="1.0.0",
        modes=[Mode(mode_id="build", name="Build", tools=["edit"], validators=["judge"])],
        validators=[spec], initial_mode="build", disagreement_policy="unanimous",
    )
    completion = _completion(subject)
    workspace = MagicMock()
    def validate(task, validators, shell, **kwargs):
        return run_validators(
            protocol=protocol, validators=validators, task=task,
            phase="pre_execute", completion_fn=completion, default_model="gpt-4",
            current_mode="build", workspace_mcp=workspace,
            validator_config=SimpleNamespace(max_tokens=1500, max_tool_turns=5),
        )[0]
    coder = MockAdapter()
    builder = GraphBuilder(protocol, coder=coder, validator_fn=validate, project_root=str(tmp_path))
    builder._audit = MagicMock()
    state = builder._validate_node({
        "task": {"id": "task-951", "spec": "proposal"},
        "current_mode": "build", "iteration": 1, "stage": "validate",
        "validation_results": [], "validation_token": None,
        "constraints_passed": True, "constraint_violations": [],
        "is_complete": False, "is_blocked": False, "metadata": {},
    })
    if subject != "task-951":
        assert state["is_blocked"] is True
        assert state["halt_type"] == "validator_error"
        assert not state["validation_token"]
        assert not state.get("needs_spec_authoring")
        assert not state.get("needs_recovery")
        assert state["metadata"]["pre_validation"]["validator_results"][0]["error"] is True
        assert any("Unusable submit_verdict" in str(call) for call in builder._audit.call_args_list)
    else:
        assert not state["is_blocked"]
        assert state["validation_token"]
        assert state["metadata"]["pre_validation"]["outcome"] == "passed"
    assert coder.call_count == 0


def test_hermetic_judge_echoes_prompt_subject():
    from snodo.coders.mock import mock_completion_fn
    result = LLMValidator(_spec()).evaluate(ValidatorContext(
        task=Task(id='plan/"proposal"', spec="proposal"),
        completion_fn=mock_completion_fn, model="gpt-4", workspace_mcp=MagicMock(),
        phase="pre_execute",
    ))
    assert result.error is False
    assert result.severity == "pass"
