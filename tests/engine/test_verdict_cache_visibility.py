"""A reused verdict is visibly reused in the audit trail and run output (#246).

The cache is not in the audit log — the log is append-only and hash-chained,
and nothing about the cache may alter how its events are written.  Instead the
verdict carries ``reused`` into the one canonical record that every audit and
display surface is built from, so the operator can always tell a reused
judgement from a freshly made one.
"""

from snodo.compiler.models import DisagreementPolicy, Mode, Protocol, Validator
from snodo.core.interfaces import ValidatorResult
from snodo.engine.loop import GraphBuilder
from snodo.engine.state import _build_audit_results


def _protocol():
    return Protocol(
        protocol_id="p_visible",
        name="Reuse Visibility Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=["security"],
            )
        ],
        validators=[
            Validator(
                validator_id="security",
                validator_type="security",
                criteria=["No secret in code"],
            )
        ],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )


def test_audit_results_mark_a_reused_verdict_as_reused():
    validator = _protocol().validators[0]
    reused = ValidatorResult(
        validator_id="security",
        severity="pass",
        justification="looks fine",
        reused=True,
    )
    fresh = ValidatorResult(
        validator_id="security", severity="pass", justification="looks fine"
    )

    reused_entry = _build_audit_results([validator], [reused])[0]
    fresh_entry = _build_audit_results([validator], [fresh])[0]

    assert reused_entry["reused"] is True
    assert "reused" not in fresh_entry


def test_operator_output_says_the_verdict_was_reused(capsys):
    builder = GraphBuilder(_protocol())
    result = ValidatorResult(
        validator_id="security",
        severity="warn",
        justification="one concern",
        reused=True,
    )

    builder._validator_verdict_cb("security", result)

    out = capsys.readouterr().out
    assert "security: warn" in out
    assert "(reused)" in out


def test_retry_reuses_prior_pass_for_security_and_meta_spec(tmp_path):
    import json
    from types import SimpleNamespace

    from snodo.core.interfaces import Task
    from snodo.validators.verdict_cache import VerdictCache
    from snodo.validators.runner import run_validators

    validators = [
        Validator(
            validator_id="security", validator_type="security", criteria=["Safe"]
        ),
        Validator(
            validator_id="meta_spec", validator_type="planning", criteria=["Clear"]
        ),
        Validator(
            validator_id="still_judged",
            validator_type="planning",
            criteria=["Check evidence"],
        ),
    ]
    protocol = Protocol(
        protocol_id="retry-cache",
        name="Retry cache",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=[v.validator_id for v in validators],
            )
        ],
        validators=validators,
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )
    cache = VerdictCache(tmp_path / "verdict_cache.json", project_root=str(tmp_path))
    evidence = "failed validator evidence"
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        role = (kwargs.get("metadata") or {}).get("role", "")
        severity = "warn" if "still_judged" in role else "pass"
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {"severity": severity, "justification": "ok"}
                        )
                    )
                )
            ]
        )

    def run(task):
        return run_validators(
            protocol=protocol,
            validators=validators,
            task=task,
            phase="pre_execute",
            completion_fn=completion,
            validator_config=SimpleNamespace(max_tokens=1500, max_tool_turns=2),
            current_mode="producer",
            verdict_cache=cache,
        )[0]

    first = run(Task(id="retry", spec="Implement the feature"))
    assert {result.validator_id: result.severity for result in first} == {
        "security": "pass",
        "meta_spec": "pass",
        "still_judged": "warn",
    }
    calls.clear()
    retry = Task(
        id="retry",
        spec=f"Original spec: Implement the feature\n\n{evidence}",
        root_spec="Implement the feature",
        composed_input_source="operator_retry",
    )
    second = run(retry)

    by_id = {result.validator_id: result for result in second}
    assert by_id["security"].reused is True
    assert by_id["meta_spec"].reused is True
    assert calls, "the un-cached validator should still be judged"
    assert all(
        any(evidence in message.get("content", "") for message in call["messages"])
        for call in calls
    )


def test_retry_still_judges_validator_that_warned(tmp_path):
    import json
    from types import SimpleNamespace

    from snodo.core.interfaces import Task
    from snodo.validators.verdict_cache import VerdictCache
    from snodo.validators.runner import run_validators

    validator = Validator(
        validator_id="meta_spec", validator_type="planning", criteria=["Clear"]
    )
    protocol = Protocol(
        protocol_id="retry-warn",
        name="Retry warning",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer",
                tools=["edit"],
                validators=[validator.validator_id],
            )
        ],
        validators=[validator],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )
    cache = VerdictCache(tmp_path / "verdict_cache.json", project_root=str(tmp_path))
    calls = []

    def completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {"severity": "warn", "justification": "unclear"}
                        )
                    )
                )
            ]
        )

    def run(task):
        return run_validators(
            protocol=protocol,
            validators=[validator],
            task=task,
            phase="pre_execute",
            completion_fn=completion,
            validator_config=SimpleNamespace(max_tokens=1500, max_tool_turns=2),
            current_mode="producer",
            verdict_cache=cache,
        )[0][0]

    original = Task(id="retry", spec="Implement the feature")
    retry = Task(
        id="retry",
        spec="Original spec: Implement the feature\n\nPrior evidence",
        root_spec=original.spec,
        composed_input_source="operator_retry",
    )
    first = run(original)
    first_call_count = len(calls)
    second = run(retry)

    assert first.severity == second.severity == "warn"
    assert first.reused is False and second.reused is False
    assert len(calls) > first_call_count
