from snodo.cli.commands.models_validator_compare import (
    compare_validator_verdicts,
    json_record,
    render_human,
)


def result(severity, justification="ok", **metrics):
    return {"severity": severity, "justification": justification, **metrics}


def test_pass_to_warn_is_worse():
    assert compare_validator_verdicts(result("pass"), result("warn"))["direction"] == "worse"


def test_warn_to_pass_is_better():
    assert compare_validator_verdicts(result("warn"), result("pass"))["direction"] == "better"


def test_same_severity_with_different_justification_is_same_direction():
    compared = compare_validator_verdicts(result("warn", "one"), result("warn", "two"))
    assert compared["direction"] == "same"
    assert compared["justification_differs"] is True


def test_missing_metrics_render_as_not_available():
    compared = compare_validator_verdicts(result("pass"), result("pass"))
    text = render_human(compared)
    assert "elapsed time: n/a -> n/a" in text
    assert "difference n/a" in text


def test_json_record_contains_both_sides():
    payload = json_record(compare_validator_verdicts(result("pass"), result("warn")))
    assert payload["schema"] == "snodo.models-validator-compare.v1"
    assert set(payload) == {
        "schema", "baseline_severity", "candidate_severity", "direction",
        "justification_differs", "justifications", "metrics",
    }
    assert payload["baseline_severity"] == "pass"
    assert payload["candidate_severity"] == "warn"
