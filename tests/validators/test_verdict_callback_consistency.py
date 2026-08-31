"""The progress callback (operator's terminal view) must never report a
severity that the audit record does not.

Regression guard: the runner used to invoke ``progress_cb`` *before* applying
severity caps, so the operator saw ``blocker`` while the record stored
``severity: warn`` with ``severity_original: blocker`` — two surfaces, two
different primary severities for the same validator on the same run.
"""

from unittest.mock import MagicMock

import pytest

from snodo.compiler.models import Severity, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.loop import GraphBuilder
from snodo.engine.state import _build_audit_results
from snodo.validators.runner import run_validators


def _protocol_with_cap(cap):
    v = Validator(
        validator_id="quality",
        validator_type="security",
        evaluation_phase="pre_execute",
        severity_cap=cap,
        criteria=["check"],
    )
    protocol = MagicMock()
    protocol.get_mode.return_value = MagicMock(
        name="producer", tools=[], transitions={}, validators=["quality"]
    )
    return protocol, v


def _run(cap, dispatch, *, task=None, progress_seen=None):
    protocol, v = _protocol_with_cap(cap)
    results, cap_originals = run_validators(
        protocol=protocol,
        validators=[v],
        task=task or Task(id="t1", spec="test"),
        phase="pre_execute",
        completion_fn=None,
        validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
        current_mode="producer",
        dispatch_fn=dispatch,
        progress_cb=progress_seen,
    )
    record = _build_audit_results([v], results, cap_originals)
    return results, cap_originals, record


@pytest.mark.parametrize(
    "cap, raw_severity",
    [
        (Severity.WARN, "blocker"),  # capped blocker -> warn
        (Severity.PASS, "blocker"),  # capped blocker -> pass
        (Severity.PASS, "warn"),  # capped warn -> pass
        (None, "blocker"),  # not capped
        (Severity.WARN, "warn"),  # at cap, not downgraded
    ],
)
def test_callback_severity_matches_record(cap, raw_severity):
    """The severity handed to progress_cb equals the severity in the audit record."""
    seen = {}

    def cb(vid, result):
        seen[vid] = (result.severity, getattr(result, "severity_original", None))

    def dispatch(v_spec, ctx, reg):
        return ValidatorResult(
            validator_id=v_spec.validator_id,
            severity=raw_severity,
            justification="finding",
            cited_criteria=["[Criterion 1] check"],
        )

    results, cap_originals, record = _run(cap, dispatch, progress_seen=cb)

    assert len(record) == 1
    entry = record[0]
    # PRIMARY invariant: the two surfaces agree on the reported severity.
    assert seen["quality"][0] == entry["severity"], (
        f"callback reported {seen['quality'][0]!r} but record stored "
        f"{entry['severity']!r}"
    )
    # SECONDARY invariant: when a cap was applied, both surfaces carry the same
    # pre-cap value (or both carry None) — no contradiction about capping.
    assert seen["quality"][1] == entry.get("severity_original"), (
        f"callback reported original {seen['quality'][1]!r} but record stored "
        f"{entry.get('severity_original')!r}"
    )


def test_recovery_cap_callback_matches_record():
    """The pre-execute recovery cap path also agrees across both surfaces."""
    seen = {}

    def cb(vid, result):
        seen[vid] = (result.severity, getattr(result, "severity_original", None))

    def dispatch(v_spec, ctx, reg):
        return ValidatorResult(
            validator_id=v_spec.validator_id,
            severity="blocker",
            justification="recovered finding",
        )

    # depth > 0 makes this a recovery task, triggering the pre-execute cap path.
    results, cap_originals, record = _run(
        None, dispatch, task=Task(id="t1", spec="test", depth=1), progress_seen=cb
    )

    entry = record[0]
    assert entry["severity"] == "pass"
    assert entry["severity_original"] == "blocker"
    assert seen["quality"][0] == entry["severity"]
    assert seen["quality"][1] == entry["severity_original"]


def test_operator_shows_both_values_when_capped():
    """The terminal verdict labels the stored (post-cap) severity but still
    names the pre-cap value, so it never contradicts the record."""
    messages = []

    def mock_progress(msg, verbose=False):
        messages.append(msg)

    loop = MagicMock(spec=GraphBuilder)
    loop._verbose = True
    loop._progress = mock_progress

    capped = ValidatorResult(
        validator_id="quality",
        severity="warn",
        justification="critical issue downgraded",
        severity_original="blocker",
    )
    GraphBuilder._validator_verdict_cb(loop, "quality", capped)

    assert len(messages) == 1
    line = messages[0]
    # The primary label matches the record (post-cap), never the pre-cap value.
    assert "quality: warn" in line
    assert "blocker" not in line.split("—")[0].replace("from blocker", "")
    # The original is still surfaced rather than hidden.
    assert "from blocker" in line
