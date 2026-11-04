import json

import pytest

from snodo.task_usage_summary import print_task_usage_summary


def _write(tmp_path, records):
    folder = tmp_path / ".snodo" / "tasks" / "t"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(json.dumps({"usage": records}))


@pytest.mark.parametrize("outcome", ["completed", "blocked"])
def test_completed_and_blocked_task_usage_prints_role_rows_and_total(
    tmp_path, capsys, outcome
):
    # The summary is outcome-independent and is printed after the task's existing
    # terminal line, as it is at the _execute_task call site.
    _write(tmp_path, [
        {"role": "coder", "input_tokens": 10, "output_tokens": 5, "duration_ms": 1000},
        {"role": "validator:test", "input_tokens": 20, "output_tokens": 4, "duration_ms": 1000},
    ])
    print(f"Task {outcome}.")
    print_task_usage_summary(tmp_path, "t", 3)
    output = capsys.readouterr().out
    assert output.index(f"Task {outcome}.") < output.index("Usage:")
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


def test_malformed_usage_list_is_ignored_without_raising(tmp_path, capsys):
    _write(tmp_path, [None, "corrupt", {"role": "coder", "input_tokens": 2, "output_tokens": 1}])
    print_task_usage_summary(tmp_path, "t", 1)
    output = capsys.readouterr().out
    assert "coder: 2 | 1" in output
    assert "total: 2 | 1 | 1.0s" in output


@pytest.mark.parametrize("state_text", ["not json", '{"usage": null}', "[]"])
def test_unreadable_or_non_list_usage_prints_nothing(tmp_path, capsys, state_text):
    folder = tmp_path / ".snodo" / "tasks" / "t"
    folder.mkdir(parents=True)
    (folder / "state.json").write_text(state_text)

    print_task_usage_summary(tmp_path, "t", 1)

    assert capsys.readouterr().out == ""
