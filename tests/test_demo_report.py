"""Tests for demo end-of-run reporting."""

import subprocess

from demo.report import collect_patch, format_report


def _record(**changes):
    record = {
        "arm": "governed",
        "model": "provider/model",
        "task_id": "task-1",
        "outcome": "passed",
        "halt_type": None,
        "wall_seconds": 12.5,
        "input_tokens": 100,
        "output_tokens": 50,
        "cost_usd": 0.01,
        "cost_source": "recorded_usage",
        "worktree_path": None,
        "validator_findings": [],
    }
    return record | changes


def test_report_lists_validator_findings():
    report = format_report(
        _record(
            validator_findings=[
                {
                    "validator_id": "tests",
                    "severity": "warning",
                    "justification": "One check needs attention",
                }
            ]
        ),
        "",
        {"status": "passed", "reason": "ok"},
    )
    assert "- tests [warning]: One check needs attention" in report


def test_report_shows_not_available_for_null_values():
    report = format_report(
        _record(
            wall_seconds=None, input_tokens=None, output_tokens=None, cost_usd=None
        ),
        "",
        {"status": "not_run", "reason": None},
    )
    assert "Time: n/a seconds" in report
    assert "Input tokens: n/a" in report
    assert "Output tokens: n/a" in report
    assert "Cost: n/a USD" in report
    assert "Verification reason: n/a" in report


def test_report_includes_patch_text():
    assert "+print('hello')" in format_report(
        _record(),
        "diff --git a/a.py b/a.py\n+print('hello')",
        {"status": "passed", "reason": "ok"},
    )


def test_report_states_verification_status():
    report = format_report(_record(), "", {"status": "failed", "reason": "test failed"})
    assert "Verification: failed" in report
    assert "Verification reason: test failed" in report


def test_collect_patch_returns_diff_from_base(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    file_path = tmp_path / "sample.txt"
    file_path.write_text("base\n")
    subprocess.run(["git", "add", "sample.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=tmp_path, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    file_path.write_text("base\nchanged\n")
    subprocess.run(["git", "commit", "-qam", "change"], cwd=tmp_path, check=True)
    assert "+changed" in collect_patch(tmp_path, base)


def test_collect_patch_missing_folder_is_empty(tmp_path):
    assert collect_patch(tmp_path / "missing", "HEAD") == ""
