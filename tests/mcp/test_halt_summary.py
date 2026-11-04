from snodo.jobs.halt_summary import format_halt_summary


def test_orders_validators_and_shows_truncated_count():
    payload = {"reason": "halted", "validator_results": [
        {"id": f"v{i}", "severity": severity, "justification": "why"}
        for i, severity in enumerate(("warn", "blocker", "pass", "warn"))
    ]}
    result = format_halt_summary(payload)
    assert result.splitlines() == [
        "halted", "v1 (blocker): why", "v0 (warn): why", "v3 (warn): why", "and 1 more"
    ]


def test_sanitizes_controls_markup_and_bounds_text():
    result = format_halt_summary({"reason": "", "validator_results": [{
        "id": "v", "severity": "warn", "justification": "hello\n**@here** <x>"
    }]})
    assert "\n" not in result
    assert "@here" not in result
    assert "**" not in result
    assert "…" not in format_halt_summary({"reason": "short"})
    long = format_halt_summary({"reason": "x" * 1000})
    assert len(long) <= 600 and long.endswith("…")


def test_malformed_payloads_are_empty():
    for payload in (None, [], "bad", {"validator_results": [None, {}]}, {"reason": 4}):
        assert format_halt_summary(payload) == ""
