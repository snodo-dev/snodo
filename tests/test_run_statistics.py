import json
from datetime import datetime, timezone

from snodo.run_statistics import summarize_plan_run, summarize_task, summarize_window


def _state(*, outcome="resolved", halt_type=None, attempts=None, cost=0, usage=None,
           timestamp="2026-01-02T00:00:00+00:00", delivered=False):
    return {
        "status": "completed",
        "started_at": timestamp,
        "halt": {
            "final_decision": outcome,
            "halt_type": halt_type,
            "attempts": {"total": len(attempts or []), "coder_dispatches": len(attempts or []), "history": attempts or []},
        },
        "cost": {"tokens": 10, "duration_seconds": 2.5, **({} if cost is None else {"cost_usd": cost}), "provenance": "provider_reported"},
        "usage": usage or [],
        "delivered_branch": "task-branch" if delivered else None,
    }


def _write(directory, state):
    directory.mkdir(parents=True)
    (directory / "state.json").write_text(json.dumps(state), encoding="utf-8")


def test_plan_run_mixed_outcomes_and_totals(tmp_path):
    _write(tmp_path / "one", _state(attempts=[{"attempt": 1, "outcome": "resolved"}], delivered=True))
    _write(tmp_path / "two", _state(outcome="blocked", halt_type="validator_error", attempts=[{"attempt": 1, "outcome": "rejected"}, {"attempt": 2, "outcome": "resolved"}], cost=None))
    summary = summarize_plan_run(tmp_path)
    assert summary["task_count"] == 2
    assert summary["outcomes"] == {"resolved": 1, "blocked": 1}
    assert summary["halt_types"] == {"none": 1, "validator_error": 1}
    assert summary["totals"]["unknown_cost_runs"] == 1
    assert summary["totals"]["cost_usd"] == 0
    assert summary["tasks"][0]["delivered"] is True


def test_first_pass_is_based_on_first_recorded_attempt():
    state = _state(attempts=[{"attempt": 1, "outcome": "rejected"}, {"attempt": 2, "outcome": "resolved"}])
    assert summarize_task("task", state).first_pass is False


def test_window_filters_and_reports_medians_and_rate(tmp_path):
    _write(tmp_path / "inside", _state(attempts=[{"outcome": "resolved"}], timestamp="2026-01-02T00:00:00+00:00"))
    _write(tmp_path / "outside", _state(timestamp="2025-12-31T00:00:00+00:00"))
    result = summarize_window(tmp_path, datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 3, tzinfo=timezone.utc))
    assert result["task_count"] == 1
    assert result["first_pass_rate"] == 1
    assert result["totals"]["median_tokens"] == 10
    assert result["totals"]["median_duration_seconds"] == 2.5


def test_unknown_cost_differs_from_zero_and_inplace_usage_is_measured():
    zero = summarize_task("zero", _state(cost=0))
    unknown = summarize_task("unknown", _state(cost=None))
    inplace_state = _state(cost=None, usage=[{"source": "inplace_coder", "measured": [{"input_tokens": 7, "output_tokens": 3}], "total_tokens": 10}])
    inplace = summarize_task("inplace", inplace_state)
    assert zero.cost_usd == 0
    assert unknown.cost_usd is None
    assert inplace.tokens == 10
    assert inplace.cost_usd is None
