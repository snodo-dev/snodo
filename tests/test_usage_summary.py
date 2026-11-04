from snodo.usage_summary import render_usage_summary, summarize_usage


def test_litellm_and_in_place_records_aggregate_with_fallbacks():
    summary = summarize_usage([
        {"role": "coder", "input_tokens": None, "output_tokens": None,
         "prompt_tokens": 100, "completion_tokens": 25, "duration_ms": 5000},
        {"role": "coder", "input_tokens": 50, "output_tokens": 10,
         "prompt_tokens": None, "completion_tokens": None, "duration_ms": 1000},
    ])
    coder = summary["rows"][0]
    assert (coder["input_tokens"], coder["output_tokens"]) == (150, 35)
    assert coder["calls"] == 2
    assert coder["output_tokens_per_second"] == 35 / 6
    assert coder["rate_source"] == "calculated"


def test_unknown_tokens_and_missing_or_zero_duration_are_not_zero_rates():
    summary = summarize_usage([
        {"role": "validator", "input_tokens": None, "output_tokens": None, "duration_ms": 0},
        {"role": "validator", "duration_ms": None},
    ])
    row = summary["rows"][0]
    assert row["input_tokens"] is None
    assert row["output_tokens"] is None
    assert row["output_tokens_per_second"] is None
    assert row["time_seconds"] == 0


def test_reported_rate_precedes_calculation():
    row = summarize_usage([{
        "role": "coder", "output_tokens": 60, "duration_ms": 1000,
        "output_tokens_per_second": 42.5,
    }])["rows"][0]
    assert row["output_tokens_per_second"] == 42.5
    assert row["rate_source"] == "reported"


def test_roles_and_recon_agents_get_separate_rows_and_render():
    summary = summarize_usage([
        {"role": "coder", "input_tokens": 10, "output_tokens": 5, "duration_ms": 1000},
        {"role": "validator:adr", "input_tokens": 20, "output_tokens": 4, "duration_ms": 2000},
        {"agent": "recon:api", "input_tokens": 8, "output_tokens": 2, "duration_ms": 500},
    ], total_elapsed_seconds=9)
    assert [row["role"] for row in summary["rows"]] == ["coder", "validator:adr", "recon:api"]
    assert summary["total_elapsed_seconds"] == 9
    lines = render_usage_summary(summary)
    assert lines[0].startswith("Usage:")
    assert "recon:api: 8 | 2 | 0.5s | 4.0 C" in lines
    assert summary["total"]["time_seconds"] == 3.5
    assert lines[-1].startswith("total: 38 | 11 | 3.5s |")
    assert render_usage_summary(summary, 9)[-1].startswith("total: 38 | 11 | 9.0s |")
    assert len(lines) == 5
