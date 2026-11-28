"""Regression coverage for CLI timing diagnostics in the e2e fixture."""

import importlib.util
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
CONFTEST = ROOT / "tests" / "e2e" / "conftest.py"


def test_failed_test_reports_cli_timings_and_redacts_secrets(tmp_path, capsys, monkeypatch):
    spec = importlib.util.spec_from_file_location("e2e_timing_conftest", CONFTEST)
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)

    def stand_in_run(*args, **kwargs):
        if "queue" in args[0]:
            raise subprocess.TimeoutExpired(args[0], timeout=1)
        return subprocess.CompletedProcess(args[0], 0, stdout="", stderr="")

    monkeypatch.setattr(plugin.subprocess, "run", stand_in_run)
    failing_test = tmp_path / "test_failure.py"
    failing_test.write_text(
        "def test_failure(snodo_cli):\n"
        "    snodo_cli(['cloud', 'connect', '--api-key', 'never-' + 'print-this'])\n"
        "    snodo_cli(['queue', 'inspect'])\n"
    )

    result = pytest.main(
        ["-q", "-o", "addopts=", str(failing_test)],
        plugins=[plugin],
    )
    output = capsys.readouterr().out

    assert result == pytest.ExitCode.TESTS_FAILED
    assert "snodo_cli call timings" in output
    assert "['cloud', 'connect', '--api-key', '<redacted>']" in output
    assert "['queue', 'inspect']" in output
    assert "completed," in output
    assert "in flight," in output
    assert "never-print-this" not in output
