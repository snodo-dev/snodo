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


def test_epoch_timestamps_report_elapsed_seconds(tmp_path):
    state = {"started_at": 1_700_000_000.0, "completed_at": 1_700_001_318.0}
    metrics = comparison_metrics(tmp_path, {"metrics": state}, state, "candidate")
    assert metrics["baseline"]["elapsed_seconds"] == 1318.0
    assert metrics["candidate"]["elapsed_seconds"] == 1318.0


def test_baseline_job_state_is_loaded_from_task_record(tmp_path):
    task_dir = tmp_path / ".snodo" / "tasks" / "task-1"
    task_dir.mkdir(parents=True)
    (task_dir / "state.json").write_text('{"job_id":"job_1"}', encoding="utf-8")
    job_dir = tmp_path / ".snodo" / "jobs" / "job_1"
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text('{"wall_time_ms":2500}', encoding="utf-8")

    metrics = comparison_metrics(tmp_path, {"task": "task-1"}, {}, "candidate")

    assert metrics["baseline"]["elapsed_seconds"] == 2.5
    assert metrics["baseline"]["coder_turns"] is None
    assert metrics["candidate"]["elapsed_seconds"] is None


def test_malformed_and_non_object_baseline_state_is_unknown(tmp_path):
    task_dir = tmp_path / ".snodo" / "tasks" / "task-1"
    task_dir.mkdir(parents=True)
    (task_dir / "state.json").write_text("[]", encoding="utf-8")

    malformed = comparison_metrics(
        tmp_path,
        {"metrics": {"started_at": "not-a-date", "completed_at": "also-not-a-date"}},
        {"elapsed_ms": "missing", "wall_time_ms": 0, "validator_results": [None, {"severity": "unknown"}]},
        "candidate",
    )

    assert malformed["baseline"]["elapsed_seconds"] is None
    assert malformed["candidate"]["elapsed_seconds"] == 0
    assert malformed["candidate"]["post_validation_findings"] == {
        "blocker": 0,
        "warn": 0,
        "escalate": 0,
        "pass": 0,
        "validator_error": 0,
        "internal_error": 0,
    }


def test_missing_candidate_metrics_report_unknown_values(tmp_path):
    metrics = comparison_metrics(tmp_path, {"metrics": {}}, {"usage": "not-recorded"}, "candidate")

    assert metrics["candidate"]["coder_time_seconds"] is None
    assert metrics["candidate"]["coder_turns"] is None
    assert metrics["candidate"]["cost"] is None
    assert metrics["candidate"]["post_validation_findings"] is None
    assert metrics["token_comparability_note"]


def test_elapsed_ms_takes_precedence_over_wall_time(tmp_path):
    metrics = comparison_metrics(
        tmp_path,
        {"metrics": {"elapsed_ms": 1250, "wall_time_ms": 9000}},
        {},
        "candidate",
    )

    assert metrics["baseline"]["elapsed_seconds"] == 1.25


def test_non_object_baseline_task_state_and_job_state_are_unknown(tmp_path):
    task_dir = tmp_path / ".snodo" / "tasks" / "task-1"
    task_dir.mkdir(parents=True)
    (task_dir / "state.json").write_text('{"job_id":"job-1"}', encoding="utf-8")
    job_dir = tmp_path / ".snodo" / "jobs" / "job-1"
    job_dir.mkdir(parents=True)
    (job_dir / "state.json").write_text("[]", encoding="utf-8")

    metrics = comparison_metrics(tmp_path, {"task": "task-1"}, {}, "candidate")

    assert metrics["baseline"]["elapsed_seconds"] is None
    assert metrics["baseline"]["cost"] is None
    assert metrics["baseline"]["post_validation_findings"] is None


def test_cost_falls_back_to_recorded_total_then_per_call_cost(tmp_path):
    from_total = comparison_metrics(
        tmp_path, {"metrics": {"cost": {"total": 1.75}}}, {}, "candidate"
    )
    from_calls = comparison_metrics(
        tmp_path,
        {"metrics": {"usage": [{"role": "coder", "cost": 0.4}, {"cost": 0.6}]}},
        {},
        "candidate",
    )

    assert from_total["baseline"]["cost"] == 1.75
    assert from_calls["baseline"]["cost"] == 1.0
