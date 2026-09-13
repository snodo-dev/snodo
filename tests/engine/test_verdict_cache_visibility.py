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
