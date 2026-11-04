from snodo.compare_metrics import comparison_metrics


def test_full_metrics_and_missing_baseline_job(tmp_path):
    state = {
        "usage": [
            {"role": "coder", "input_tokens": 10, "output_tokens": 20, "duration_ms": 2000},
            {"role": "validator", "input_tokens": 3, "output_tokens": 4, "duration_ms": 500},
        ],
        "halt": {"coder_report": {"turns_used": 2, "wall_time_ms": 1500},
                 "validator_results": [{"severity": "warn"}, {"severity": "blocker"}]},
        "wall_time_ms": 9000, "total_cost": 0.5,
    }
    metrics = comparison_metrics(tmp_path, {"task": "t", "metrics": state}, state, "candidate1")
    assert metrics["baseline"]["coder_time_seconds"] == 1.5
    assert metrics["candidate"]["coder_turns"] == 2
    assert metrics["candidate"]["validator_output_tokens"] == 4
    assert metrics["candidate"]["post_validation_findings"]["warn"] == 1
    assert metrics["candidate"]["cost"] == 0.5


def test_absent_or_failed_evidence_is_unknown_not_zero(tmp_path):
    metrics = comparison_metrics(tmp_path, {"task": "gone"}, {"status": "failed", "halt": {}}, "job")
    for side in ("baseline", "candidate"):
        assert metrics[side]["coder_time_seconds"] is None
        assert metrics[side]["coder_input_tokens"] is None
        assert metrics[side]["cost"] is None
        assert metrics[side]["post_validation_findings"] is None


def test_failed_candidate_keeps_partial_usage_but_unknown_findings(tmp_path):
    candidate = {"status": "failed", "usage": [{"role": "coder", "input_tokens": 7, "output_tokens": 2}]}
    metrics = comparison_metrics(tmp_path, {"task": "gone"}, candidate, "job")
    assert metrics["candidate"]["coder_input_tokens"] == 7
    assert metrics["candidate"]["post_validation_findings"] is None
