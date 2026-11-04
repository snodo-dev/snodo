import json

from snodo.task_usage_summary import print_task_usage_summary


def _write(tmp_path, records):
    folder = tmp_path / ".snodo" / "tasks" / "t"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps({"usage": records}))


def test_completed_and_blocked_usage_prints_role_rows_and_total(tmp_path, capsys):
    _write(tmp_path, [
        {"role": "coder", "input_tokens": 10, "output_tokens": 5, "duration_ms": 1000},
        {"role": "validator:test", "input_tokens": 20, "output_tokens": 4, "duration_ms": 1000},
    ])
    print_task_usage_summary(tmp_path, "t", 3)
    output = capsys.readouterr().out
    assert "coder: 10 | 5" in output
    assert "validator:test: 20 | 4" in output
    assert "total: 30 | 9 | 3.0s" in output


def test_unknown_coder_tokens_and_no_usage(tmp_path, capsys):
    _write(tmp_path, [{"role": "coder"}])
    print_task_usage_summary(tmp_path, "t", 1)
    assert "coder: n/a | n/a" in capsys.readouterr().out
    (tmp_path / ".snodo" / "tasks" / "t" / "state.json").write_text('{"usage": []}')
    print_task_usage_summary(tmp_path, "t", 1)
    assert capsys.readouterr().out == ""
