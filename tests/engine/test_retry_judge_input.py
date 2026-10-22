"""Retry failure reports are evidence, not operator-authored proposals."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.nodes.writeback import WritebackMixin, _build_attempt_summary
from snodo.engine.state import LoopState
from snodo.validators.context import ValidatorContext
from snodo.validators.llm_validator import LLMValidator
from snodo.validators.runner import run_validators


SPEC = "Make ingestion reject requests above the per-key rate limit."
EVIDENCE = "src/limiter.py: 742 lines; gate: expected <= 500 lines"


def protocol():
    return Protocol(
        protocol_id="retry-test", name="Retry test", version="1.0.0",
        initial_mode="producer",
        modes=[Mode(mode_id="producer", name="Producer", tools=["edit"],
                    validators=["spec"])],
        validators=[Validator(validator_id="spec", validator_type="planning",
                              criteria=["Do not prescribe implementation details."])],
    )


def test_real_retry_composition_reaches_quorum_without_becoming_spec(tmp_path, monkeypatch):
    from snodo.cli.commands.run_cmd import RunArgs, _retry_task
    from snodo.infrastructure.session import SessionManager
    from snodo.infrastructure.state import ProjectState, write_state

    p = protocol()
    write_state(str(tmp_path), ProjectState(current_mode="producer"))
    mgr = SessionManager(sessions_dir=tmp_path / ".snodo" / "sessions")
    session = mgr.create_session("producer", str(tmp_path))
    mgr.update_decision(session.session_id, "task_failure", {
        "rate": {"original_spec": SPEC, "attempt": 1, "phase": "post_execute",
                 "failed_validators": [{"validator_id": "gate", "justification": EVIDENCE}],
                 "files_changed": ["src/limiter.py"]},
    })
    monkeypatch.setattr("snodo.cli.commands.run_cmd.load_protocol", lambda path: p)
    seen = []

    def dispatch(v, context, registry):
        seen.append(context)
        return ValidatorResult(
            validator_id=v.validator_id,
            severity="pass" if context.task.spec == SPEC else "blocker",
            justification="judged only the proposal",
        )

    def judge(task, phase="pre_execute"):
        results, _ = run_validators(
            p, p.validators, task, phase=phase, current_mode="producer",
            validator_config=SimpleNamespace(max_tokens=1500, max_tool_turns=2),
            dispatch_fn=dispatch,
        )
        return results[0]

    assert judge(Task(id="rate", spec=SPEC)).severity == "pass"

    def execute(args, prot, task, model):
        assert task.spec.startswith(f"Original spec: {SPEC}")
        assert EVIDENCE in task.spec
        assert "Files changed in previous attempt: src/limiter.py" in task.spec
        assert "Fix the issues above." in task.spec
        assert judge(task).severity == "pass"
        assert seen[-1].task.spec == SPEC
        assert EVIDENCE in seen[-1].retry_evidence
        # The coder and post-execute judges retain the composed input.
        assert EVIDENCE in task.spec
        judge(task, "post_execute")
        assert seen[-1].task is task
        assert seen[-1].retry_evidence == ""
        return 0

    monkeypatch.setattr("snodo.cli.commands.run_cmd._execute_task", execute)
    assert _retry_task(RunArgs(model="mock-model"), "rate", str(tmp_path), mgr) == 0


@pytest.mark.parametrize("tools", [False, True])
def test_evidence_reaches_completion_separately_for_both_judge_paths(tools):
    task = Task(id="rate", spec=f"Original spec: {SPEC}\n\n{EVIDENCE}", root_spec=SPEC)
    completion = MagicMock(return_value="response")
    context = ValidatorContext(task=task, phase="pre_execute", completion_fn=completion)
    validator = LLMValidator(protocol().validators[0])
    prompt = (validator._build_tool_loop_prompt(context, {"read_file"}) if tools
              else validator._build_prompt(context))
    assert EVIDENCE not in prompt
    messages = [{"role": "user", "content": prompt}]
    context.completion_fn(messages=messages)
    sent = completion.call_args.kwargs["messages"]
    assert sent[0]["content"] == prompt
    assert EVIDENCE in sent[-1]["content"]
    assert "not the specification" in sent[-1]["content"]
    assert "prescriptiveness criteria" in sent[-1]["content"]
    assert len(messages) == 1
    assert context.task.spec == SPEC
    assert task.spec != SPEC


@pytest.mark.parametrize("previous", [0, 2])
def test_refusal_preserves_coding_summary_not_failure_sequence(previous):
    decisions = {"task_failure": {"rate": {"attempt": previous, "original_spec": SPEC}}}
    session = SimpleNamespace(checkpoint=SimpleNamespace(decisions=decisions))
    manager = MagicMock()
    manager.load_session.return_value = session

    def update(session_id, key, value):
        decisions[key] = value

    manager.update_decision.side_effect = update
    writer = WritebackMixin()
    writer._session_manager = manager
    writer._session_id = "session"
    state = LoopState(task=Task(id="rate", spec=SPEC, depth=previous), current_mode="producer",
                      is_blocked=True, halt_type="blocked")
    results = [ValidatorResult(validator_id="spec", severity="blocker", justification="refused")]
    writer._auto_write_failure_context(state, results)
    # Failure records keep their established sequence, independently of the
    # narrative's count of judged coding attempts.
    assert decisions["task_failure"]["rate"]["attempt"] == previous + 1
    assert decisions["task_failure"]["rate"]["phase"] == "pre_execute"
    before = _build_attempt_summary(state, "pre_execute")
    assert before["total"] == previous
    assert before["coder_dispatches"] == previous
    payload = writer._build_halt_payload(state)
    assert payload["phase"] == "pre_execute"
    assert payload["attempts"] == before
    assert "no coder attempt was consumed" in payload["hint"]

    state.metadata["post_validation"] = {"outcome": "blocked"}
    state.validation_results = results
    writer._auto_write_failure_context(state, results)
    assert decisions["task_failure"]["rate"]["attempt"] == previous + 2
    after = _build_attempt_summary(state, "post_execute")
    assert after["total"] == previous + 1
    assert after["coder_dispatches"] == previous + 1
    assert after["history"][:-1] == before["history"]
    assert after["history"][-1] == {"attempt": previous + 1, "outcome": "blocked"}
    payload = writer._build_halt_payload(state)
    assert payload["phase"] == "post_execute"
    assert payload["attempts"] == after
    assert "no coder attempt was consumed" not in payload["hint"]
