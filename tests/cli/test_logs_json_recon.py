"""Machine-readable recon log output tests."""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from snodo.cli.commands.logs_cmd import logs_command


def _fixture(tmp_path: Path) -> str:
    recon = tmp_path / ".snodo" / "recons" / "rec_test"
    recon.mkdir(parents=True)
    (recon / "state.json").write_text(json.dumps({
        "status": "complete", "created_at": 10, "completed_at": 16,
        "agents": ["a", "b"],
    }))
    (recon / "results.json").write_text(json.dumps([
        {"agent": "a", "model": "m-a", "served_model": "served-a", "status": "complete",
         "usage": [{"input_tokens": 10, "output_tokens": 5, "duration_ms": 1000}]},
        {"agent": "b", "model": "m-b", "status": "complete",
         "usage": [{"input_tokens": 20, "output_tokens": 15, "duration_ms": 2000}]},
    ]))
    return str(tmp_path)


def test_recon_json_has_usage_totals_and_matches_usage_renderer(tmp_path, capsys):
    root = _fixture(tmp_path)
    with patch("snodo.infrastructure.paths.require_project_root", return_value=root):
        assert logs_command(SimpleNamespace(composite_id="rec_test", watch=False, json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "snodo.logs.v1"
    assert payload["ok"] is True
    assert payload["kind"] == "recon"
    assert payload["totals"] == {
        "input_tokens": 30, "output_tokens": 20,
        "model_call_time_seconds": 3.0,
        "output_tokens_per_second": 20 / 3,
    }
    assert payload["agents"][0]["served_model"] == "served-a"

    with patch("snodo.infrastructure.paths.require_project_root", return_value=root):
        from snodo.cli.commands.logs_cmd import _print_recon_usage
        results = json.loads((tmp_path / ".snodo/recons/rec_test/results.json").read_text())
        state = json.loads((tmp_path / ".snodo/recons/rec_test/state.json").read_text())
        _print_recon_usage(results, state)
    text = capsys.readouterr().out
    assert "total: 30 | 20 | 6.0s | 6.7 C" in text


def test_unknown_recon_json_emits_uniform_error(tmp_path, capsys):
    (tmp_path / ".snodo/recons").mkdir(parents=True)
    with patch("snodo.infrastructure.paths.require_project_root", return_value=str(tmp_path)):
        result = logs_command(SimpleNamespace(composite_id="rec_missing", watch=False, json=True))
    payload = json.loads(capsys.readouterr().out)
    assert result != 0
    assert payload["schema"] == "snodo.logs.v1"
    assert payload["ok"] is False
    assert "error" in payload


def test_json_watch_emits_uniform_error(tmp_path, capsys):
    with patch("snodo.infrastructure.paths.require_project_root", return_value=str(tmp_path)):
        result = logs_command(SimpleNamespace(composite_id="rec_test", watch=True, json=True))
    payload = json.loads(capsys.readouterr().out)
    assert result != 0
    assert payload["schema"] == "snodo.logs.v1"
    assert payload["ok"] is False
    assert "error" in payload
