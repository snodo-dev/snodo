"""Task run usage summary source selection."""

import json

from snodo.task_usage_summary import print_task_usage_summary


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def _job(root, job_id, usage):
    _write_json(root / ".snodo" / "jobs" / job_id / "state.json", {"usage": usage})


def test_empty_task_usage_uses_only_matching_job_records(tmp_path, capsys):
    _write_json(tmp_path / ".snodo/tasks/task_a/state.json", {"usage": []})
    _job(tmp_path, "j_a", [
        {"task_id": "task_a", "role": "coder", "input_tokens": 10, "output_tokens": 2},
        {"task_id": "task_b", "role": "validator:other", "input_tokens": 99, "output_tokens": 9},
    ])

    print_task_usage_summary(tmp_path, "task_a", 12)

    out = capsys.readouterr().out
    assert "coder: 10 | 2 | n/a | n/a" in out
    assert "total: 10 | 2 | 12.0s | n/a" in out
    assert "validator:other" not in out


def test_task_state_usage_remains_authoritative(tmp_path, capsys):
    _write_json(tmp_path / ".snodo/tasks/task_a/state.json", {
        "usage": [{"role": "coder", "input_tokens": 3, "output_tokens": 1}],
    })
    _job(tmp_path, "j_a", [{"task_id": "task_a", "role": "validator", "input_tokens": 8}])

    print_task_usage_summary(tmp_path, "task_a", 4)

    out = capsys.readouterr().out
    assert "coder: 3 | 1 | n/a | n/a" in out
    assert "validator" not in out


def test_no_usage_prints_nothing(tmp_path, capsys):
    _write_json(tmp_path / ".snodo/tasks/task_a/state.json", {"usage": []})
    _job(tmp_path, "j_a", [{"task_id": "task_b", "role": "coder"}])

    print_task_usage_summary(tmp_path, "task_a", 1)

    assert capsys.readouterr().out == ""


def test_recovery_round_usage_is_included(tmp_path, capsys):
    _write_json(tmp_path / ".snodo/tasks/task_a/state.json", {"usage": []})
    _job(tmp_path, "j_a", [
        {"task_id": "task_a_fix_2", "role": "validator:security", "input_tokens": 7, "output_tokens": 2},
    ])

    print_task_usage_summary(tmp_path, "task_a", 5)

    assert "validator:security: 7 | 2" in capsys.readouterr().out
