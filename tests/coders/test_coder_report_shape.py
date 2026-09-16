"""A coder report is an optional, best-effort evidence shape — never a verdict.

The ticket adds only the shape: no adapter fills it in and nothing reads it.
These tests pin the three properties the shape is built on (Fixes #315):

  * a report with every field absent is VALID — absence is normal, never an
    error, because a coder may do the work and forget to report it;
  * a malformed report is REJECTED WITHOUT RAISING — a coder's misstatement
    is a non-deterministic participant's noise, discarded with a log line,
    never a halt;
  * the STOP-REASON set is CLOSED — the ``Literal`` and the runtime
    ``frozenset`` agree, and a value outside the set does not parse.

And the governing constraint that makes the shape safe to inherit later: the
type carries no verdict field, so nothing a coder says can cause a pass.
"""

import logging

import pytest
from pydantic import ValidationError

from snodo.coders.report import (
    STOP_REASONS,
    CoderFileChange,
    CoderReport,
    parse_coder_report,
)

_REPORT_LOGGER = "snodo.coders.report"


# ---------------------------------------------------------------------------
# Absence is valid: a report with every field absent parses.
# ---------------------------------------------------------------------------

def test_report_with_every_field_absent_is_valid():
    # No-arg construction and an explicit empty() are both the all-absent
    # report; a coder that did the work and said nothing is a valid outcome.
    for report in (CoderReport(), CoderReport.empty()):
        assert report.files is None
        assert report.turns_used is None
        assert report.turns_available is None
        assert report.tokens_used is None
        assert report.context_window is None
        assert report.wall_time_ms is None
        assert report.stop_reason is None


def test_absent_report_parses_as_valid_not_rejected():
    # A raw empty mapping is a real (empty) report, parsed cleanly and quietly.
    assert CoderReport.model_validate({}) == CoderReport.empty()


def test_parse_tolerates_absent_and_empty_without_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=_REPORT_LOGGER):
        assert parse_coder_report(None) is None
        assert parse_coder_report({}) == CoderReport.empty()
    assert caplog.records == []


# ---------------------------------------------------------------------------
# Partial reports are normal: any subset of the three sections parses.
# ---------------------------------------------------------------------------

def test_partial_report_parses_with_absent_fields_intact():
    report = CoderReport.model_validate(
        {"files": [{"path": "src/a.py", "kind": "created"}], "stop_reason": "completed"}
    )
    assert [f.kind for f in report.files] == ["created"]
    assert report.stop_reason == "completed"
    # Everything not reported stays absent, not defaulted to a value.
    assert report.turns_used is None
    assert report.tokens_used is None
    assert report.wall_time_ms is None


def test_full_report_parses_every_section():
    report = CoderReport.model_validate(
        {
            "files": [
                {"path": "src/a.py", "kind": "created"},
                {"path": "src/b.py", "kind": "modified"},
                {"path": "src/c.py", "kind": "deleted"},
            ],
            "turns_used": 5,
            "turns_available": 6,
            "tokens_used": 12000,
            "context_window": 200000,
            "wall_time_ms": 48213,
            "stop_reason": "turn_budget",
        }
    )
    assert {f.kind for f in report.files} == {"created", "modified", "deleted"}
    assert (report.turns_used, report.turns_available) == (5, 6)
    assert (report.tokens_used, report.context_window) == (12000, 200000)
    assert report.wall_time_ms == 48213
    assert report.stop_reason == "turn_budget"


def test_report_is_round_trippable():
    report = CoderReport.model_validate(
        {"files": [{"path": "x", "kind": "modified"}], "stop_reason": "abandoned"}
    )
    dumped = report.model_dump()
    assert CoderReport.model_validate(dumped) == report


# ---------------------------------------------------------------------------
# Malformed reports are discarded with a log line, never a halt.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "malformed",
    [
        # A stop reason outside the closed set.
        {"stop_reason": "magic"},
        {"stop_reason": ""},
        {"stop_reason": "COMPLETED"},
        # A file-change kind outside the closed set.
        {"files": [{"path": "a", "kind": "renamed"}]},
        # Missing a required sub-field of a file change.
        {"files": [{"path": "a"}]},
        # Wrong scalar type.
        {"turns_used": "five"},
        # An unexpected key (extra is forbidden — the shape stays closed too).
        {"severity": "pass"},
        {"verdict": "pass"},
        # Wrong top-level shape.
        ["not", "a", "mapping"],
        "a prose tail",
        42,
    ],
)
def test_malformed_report_is_rejected_without_raising(malformed, caplog):
    with caplog.at_level(logging.WARNING, logger=_REPORT_LOGGER):
        result = parse_coder_report(malformed)  # must NOT raise
    assert result is None
    assert any(r.levelno == logging.WARNING for r in caplog.records), (
        "a discarded report must leave a log line, not pass silently"
    )


def test_parse_passes_through_a_real_report_instance():
    report = CoderReport(stop_reason="completed")
    assert parse_coder_report(report) is report


def test_direct_construction_of_a_bad_stop_reason_still_fails_loud():
    # The tolerant path is parse_coder_report; direct model construction keeps
    # pydantic's strictness, so a programmer error is never silently swallowed.
    with pytest.raises(ValidationError):
        CoderReport(stop_reason="magic")


def test_a_discarded_report_is_treated_exactly_like_an_absent_one():
    # The point of discarding rather than halting: a garbled report must be
    # indistinguishable, to anything downstream, from a coder that said nothing.
    assert parse_coder_report({"stop_reason": "magic"}) is None
    assert parse_coder_report(None) is None


# ---------------------------------------------------------------------------
# The stop-reason vocabulary is closed.
# ---------------------------------------------------------------------------

def test_stop_reason_set_is_exactly_the_proposed_five():
    assert STOP_REASONS == frozenset(
        {"completed", "turn_budget", "context_budget", "provider_fault", "abandoned"}
    )


def test_every_stop_reason_member_parses():
    for reason in sorted(STOP_REASONS):
        assert parse_coder_report({"stop_reason": reason}).stop_reason == reason


def test_stop_reason_literal_and_runtime_set_agree():
    # The two declarations of the vocabulary cannot drift: the Literal the
    # model enforces and the frozenset the code checks carry the same members.
    from typing import get_args

    from snodo.coders.report import StopReason

    assert frozenset(get_args(StopReason)) == STOP_REASONS


def test_file_change_kinds_are_closed():
    for kind in ("created", "modified", "deleted"):
        assert CoderFileChange(path="a", kind=kind).kind == kind
    with pytest.raises(ValidationError):
        CoderFileChange(path="a", kind="archived")


# ---------------------------------------------------------------------------
# The shape cannot carry a verdict: nothing a coder says can cause a pass.
# ---------------------------------------------------------------------------

def test_report_type_has_no_verdict_bearing_field():
    # No pass/severity/status/halt field exists to add a verdict to later; the
    # whole point of the shape is that a coder reports evidence, not a result.
    fields = set(CoderReport.model_fields)
    forbidden = {
        "passed", "pass", "severity", "status", "verdict", "halt",
        "result", "outcome", "state",
    }
    assert not (fields & forbidden), (
        f"a coder report must not gain a verdict-bearing field: {fields & forbidden}"
    )


def test_completed_stop_reason_carries_no_verdict_of_its_own():
    # "completed" is the coder's account of stopping, not a pass: the same
    # report with no files claims nothing about what was written.
    report = parse_coder_report({"stop_reason": "completed"})
    assert report.stop_reason == "completed"
    assert report.files is None
