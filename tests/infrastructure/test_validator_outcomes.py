from datetime import UTC, datetime

from snodo.infrastructure.validator_outcomes import aggregate_validator_outcomes


def validate(timestamp, phase="pre", *, invoked=None, results=None):
    return {
        "event_type": "validate",
        "timestamp": timestamp,
        "data": {
            "phase": phase,
            "validators_invoked": invoked or [],
            "results": results or [],
        },
    }


def verdict(validator_id, severity, reused=False):
    return {"validator_id": validator_id, "severity": severity, "reused": reused}


def test_counts_severity_and_block_rate_per_validator_and_phase():
    stats = aggregate_validator_outcomes([
        validate(
            "2026-01-01T00:00:00Z",
            invoked=["alpha"],
            results=[
                verdict("alpha", "pass"),
                verdict("alpha", "blocker"),
                verdict("beta", "warn"),
            ],
        ),
        validate("2026-01-01T00:01:00Z", "post", invoked=["alpha"], results=[
            verdict("alpha", "blocker")
        ]),
    ])
    pre = stats.validators["alpha"]["pre"]
    assert dict(pre.severity_counts) == {"pass": 1, "blocker": 1}
    assert pre.block_rate == 0.5
    assert stats.validators["alpha"]["post"].block_rate == 1.0
    assert stats.validators["beta"]["pre"].severity_counts["warn"] == 1


def test_window_filters_old_events_and_preserves_invoked_without_verdict():
    stats = aggregate_validator_outcomes([
        validate(
            "2026-01-01T00:00:00Z",
            invoked=["old"],
            results=[verdict("old", "blocker")],
        ),
        validate("2026-01-09T00:00:00Z", invoked=["quiet"]),
    ], days=2, now=datetime(2026, 1, 10, tzinfo=UTC))
    assert "old" not in stats.validators
    assert stats.validators["quiet"]["pre"].verdict_count == 0
    assert stats.validators["quiet"]["pre"].block_rate is None


def test_reused_verdicts_are_separate_and_do_not_change_rate():
    stats = aggregate_validator_outcomes([validate(
        "2026-01-01T00:00:00Z", invoked=["alpha"], results=[
            verdict("alpha", "blocker"), verdict("alpha", "pass", reused=True),
        ]
    )])
    bucket = stats.validators["alpha"]["pre"]
    assert dict(bucket.severity_counts) == {"blocker": 1}
    assert dict(bucket.reused_severity_counts) == {"pass": 1}
    assert bucket.block_rate == 1.0


def test_malformed_and_older_events_are_skipped_without_crashing():
    stats = aggregate_validator_outcomes([
        {"event_type": "validate", "data": None},
        validate("2026-01-01T00:00:00Z", "legacy-phase"),
        validate("2026-01-01T00:00:00Z", results=[verdict("alpha", "unknown")]),
        {"event_type": "validate", "timestamp": "not-a-time", "data": {"phase": "pre"}},
        {"event_type": "other", "data": None},
    ], days=30, now=datetime(2026, 1, 10, tzinfo=UTC))
    assert stats.skipped_events == 4
    assert stats.validators == {}
