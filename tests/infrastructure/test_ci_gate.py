"""Tests for the CI gate (Fixes #56).

The branch's latest CI conclusion must gate a merge, and "CI has not run" must
be a distinct, visible state — never confused with "CI passed".
"""

import json
from types import SimpleNamespace

import pytest

from snodo.infrastructure.ci_gate import (
    CIGateError,
    branch_ci_conclusion,
)


def _proc(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _run_json(runs):
    return _proc(stdout=json.dumps(runs))


class TestBranchCIConclusion:
    def test_pass_when_latest_run_success(self):
        runs = [{"databaseId": 42, "status": "completed", "conclusion": "success"}]
        result = branch_ci_conclusion("/repo", "agent-a", run_gh=lambda *a, **k: _run_json(runs))
        assert result.state == "pass"
        assert result.run_id == "42"

    def test_fail_when_latest_run_failed(self):
        runs = [{"databaseId": 7, "status": "completed", "conclusion": "failure"}]
        result = branch_ci_conclusion("/repo", "agent-a", run_gh=lambda *a, **k: _run_json(runs))
        assert result.state == "fail"

    def test_fail_when_latest_run_cancelled(self):
        runs = [{"databaseId": 8, "status": "completed", "conclusion": "cancelled"}]
        result = branch_ci_conclusion("/repo", "agent-a", run_gh=lambda *a, **k: _run_json(runs))
        assert result.state == "fail"

    def test_in_progress_when_run_queued(self):
        runs = [{"databaseId": 9, "status": "queued", "conclusion": None}]
        result = branch_ci_conclusion("/repo", "agent-a", run_gh=lambda *a, **k: _run_json(runs))
        assert result.state == "in_progress"

    def test_in_progress_when_run_in_progress(self):
        runs = [{"databaseId": 10, "status": "in_progress", "conclusion": None}]
        result = branch_ci_conclusion("/repo", "agent-a", run_gh=lambda *a, **k: _run_json(runs))
        assert result.state == "in_progress"

    def test_not_run_when_no_runs(self):
        """CI has never run on the branch — a distinct state from 'pass'."""
        result = branch_ci_conclusion("/repo", "agent-a", run_gh=lambda *a, **k: _run_json([]))
        assert result.state == "not_run"
        assert "never run" in result.detail

    def test_not_run_when_completed_without_conclusion(self):
        runs = [{"databaseId": 11, "status": "completed", "conclusion": None}]
        result = branch_ci_conclusion("/repo", "agent-a", run_gh=lambda *a, **k: _run_json(runs))
        assert result.state == "not_run"

    def test_gh_failure_raises(self):
        with pytest.raises(CIGateError, match="Could not query CI"):
            branch_ci_conclusion(
                "/repo", "agent-a",
                run_gh=lambda *a, **k: _proc(stderr="gh: not authenticated", returncode=1),
            )

    def test_gh_missing_raises(self):
        def missing_gh(*a, **k):
            raise FileNotFoundError("gh")

        with pytest.raises(CIGateError, match="GitHub CLI"):
            branch_ci_conclusion("/repo", "agent-a", run_gh=missing_gh)

    def test_queries_branch_and_workflow(self):
        captured = {}

        def fake_gh(args, cwd):
            captured["args"] = args
            captured["cwd"] = cwd
            return _run_json([{"databaseId": 1, "status": "completed", "conclusion": "success"}])

        branch_ci_conclusion("/repo", "feature-x", run_gh=fake_gh)
        assert "--branch" in captured["args"]
        assert "feature-x" in captured["args"]
        assert "--workflow" in captured["args"]
        assert "ci.yml" in captured["args"]
        assert captured["cwd"] == "/repo"
