"""Contract for the shared, side-effect-free usage roll-up."""

from snodo.infrastructure.usage_summary import render_usage_summary, summarize_usage


def test_litellm_and_inplace_aliases_and_precedence():
    records = [
        {"role": "coder", "input_tokens": 12, "output_tokens": 3,
         "prompt_tokens": 999, "completion_tokens": 999, "duration_ms": 1000},
        {"role": "coder", "input_tokens": 8, "output_tokens": 2,
         "prompt_tokens": None, "completion_tokens": None, "duration_ms": 1000},
        {"role": "validator:adr", "input_tokens": None, "output_tokens": None,
         "prompt_tokens": 30, "completion_tokens": 6, "duration_ms": 3000},
    ]
    summary = summarize_usage(records, total_elapsed_seconds=20)
    coder, validator = summary["rows"]
    assert (coder["calls"], coder["input_tokens"], coder["output_tokens"]) == (2, 20, 5)
    assert (validator["input_tokens"], validator["output_tokens"]) == (30, 6)
    assert coder["time_seconds"] == 2
    assert coder["output_tokens_per_second"] == 2.5
    assert coder["rate_source"] == "calculated"
    assert summary["total"]["output_tokens"] == 11
    assert summary["total"]["time_seconds"] == 5
    assert summary["total_elapsed_seconds"] == 20


def test_unknown_counts_propagate_across_row_and_total_without_erasing_zero():
    summary = summarize_usage([
        {"role": "coder", "input_tokens": 0, "output_tokens": 0, "duration_ms": 1000},
        {"role": "coder", "input_tokens": None, "output_tokens": None, "duration_ms": 1000},
        {"agent": "search", "input_tokens": 4, "output_tokens": 2, "duration_ms": 500},
    ])
    coder, agent = summary["rows"]
    assert coder["input_tokens"] is None and coder["output_tokens"] is None
    assert coder["output_tokens_per_second"] is None
    assert agent["role"] == "search"
    assert agent["output_tokens_per_second"] == 4
    assert summary["total"]["input_tokens"] is None
    assert summary["total"]["output_tokens"] is None
    assert "coder: n/a | n/a | 2.0s | n/a" in render_usage_summary(summary)[1]
    zero = summarize_usage([{"role": "coder", "input_tokens": 0,
                             "output_tokens": 0, "duration_ms": 1000}])["total"]
    assert zero["output_tokens"] == 0
    assert zero["output_tokens_per_second"] == 0


def test_reported_rates_are_duration_weighted_and_require_usable_time():
    records = [
        {"role": "coder", "duration_ms": 1000, "output_tokens_per_second": 10},
        {"role": "coder", "duration_ms": 3000, "output_tokens_per_second": 30},
    ]
    row = summarize_usage(records)["rows"][0]
    assert row["output_tokens"] is None
    assert row["output_tokens_per_second"] == 25
    assert row["rate_source"] == "reported"
    assert "25.0 R" in render_usage_summary(summarize_usage(records))[1]
    for duration in (None, 0):
        row = summarize_usage([{"role": "coder", "duration_ms": duration,
                                "output_tokens": 4,
                                "output_tokens_per_second": 9}])["rows"][0]
        assert row["output_tokens_per_second"] is None
        assert row["rate_source"] is None


def test_mixed_rates_calculate_and_missing_duration_blocks_rate():
    records = [
        {"role": "coder", "output_tokens": 4, "duration_ms": 1000,
         "output_tokens_per_second": 20},
        {"role": "validator", "output_tokens": 6, "duration_ms": 2000},
    ]
    summary = summarize_usage(records)
    assert summary["total"]["output_tokens_per_second"] == 10 / 3
    assert summary["total"]["rate_source"] == "calculated"
    records[1]["duration_ms"] = None
    unknown_time = summarize_usage(records)
    assert unknown_time["total"]["output_tokens_per_second"] is None
    assert unknown_time["total"]["time_seconds"] is None
    assert " | n/a | n/a" in render_usage_summary(unknown_time)[-1]


def test_rendered_lines_show_model_time_and_separate_wall_time():
    summary = summarize_usage([
        {"agent": "recon:api", "input_tokens": 8, "output_tokens": 2,
         "duration_ms": 500},
    ], total_elapsed_seconds=9)
    lines = render_usage_summary(summary)
    assert len(lines) == 3
    assert lines[0].startswith("Usage:")
    assert lines[1].startswith("recon:api: 8 | 2 | 0.5s | 4.0 C")
    assert lines[2].startswith("total: 8 | 2 | 0.5s | 4.0 C")
    assert "elapsed 9.0s" in lines[2]
    assert summarize_usage([])["total"]["input_tokens"] is None
