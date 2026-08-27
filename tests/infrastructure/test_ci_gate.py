"""Tests for the CI gate (Fixes #56).

The branch's latest CI conclusion must gate a merge, and "CI has not run" must
be a distinct, visible state — never confused with "CI passed".  Conclusions
carry their context (run id, head commit, when concluded) so a stale run is
never presented as the branch's verdict, and startup_failure / cancelled /
timed_out are distinct from a plain failure.  ``wait_for_ci_conclusion`` polls
through the waiting states because right after a push GitHub has not
registered the run yet (Fixes #72).
"""

import json
from types import SimpleNamespace

import pytest

from snodo.infrastructure.ci_gate import (
    CIGateError,
    branch_ci_conclusion,
    wait_for_ci_conclusion,
)


def _proc(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


def _run_json(runs):
    return _proc(stdout=json.dumps(runs))


def _run(databaseId, status, conclusion, headSha="abc123", updatedAt="2026-08-27T00:00:00Z"):
    return {
        "databaseId": databaseId,
        "status": status,
        "conclusion": conclusion,
        "headSha": headSha,
        "updatedAt": updatedAt,
    }


class TestBranchCIConclusion:
    def test_pass_when_latest_run_success(self):
        runs = [_run(42, "completed", "success")]
        result = branch_ci_conclusion("/repo", "agent-a", run_gh=lambda *a, **k: _run_json(runs))
        assert result.state == "pass"
        assert result.run_id == "42"
        assert result.head_sha == "abc123"
        assert result.concluded_at == "2026-08-27T00:00:00Z"

    def test_fail_when_latest_run_failed(self):
        runs = [_run(7, "completed", "failure")]
        result = branch_ci_conclusion("/repo", "agent-a", run_gh=lambda *a, **k: _run_json(runs))
        assert result.state == "fail"
        assert "failure" in result.detail

    def test_startup_failure_is_distinct(self):
        """A run that never started is not the branch's fault (Fixes #74)."""
        runs = [_run(71, "completed", "startup_failure")]
        result = branch_ci_conclusion("/repo", "agent-a", run_gh=lambda *a, **k: _run_json(runs))
        assert result.state == "startup_failure"
        assert "workflow" in result.detail

    def test_cancelled_is_distinct(self):
        runs = [_run(8, "completed", "cancelled")]
        result = branch_ci_conclusion("/repo", "agent-a", run_gh=lambda *a, **k: _run_json(runs))
        assert result.state == "cancelled"

    def test_timed_out_is_distinct(self):
        runs = [_run(80, "completed", "timed_out")]
        result = branch_ci_conclusion("/repo", "agent-a", run_gh=lambda *a, **k: _run_json(runs))
        assert result.state == "timed_out"

    def test_stale_when_head_differs_from_branch_tip(self):
        """A run on an old commit says nothing about the branch now (Fixes #76)."""
        runs = [_run(7, "completed", "failure", headSha="deadbeef")]
        result = branch_ci_conclusion(
            "/repo", "agent-a", head_sha="cafebabe",
            run_gh=lambda *a, **k: _run_json(runs),
        )
        assert result.state == "stale"
        assert "deadbeef" in result.detail
        assert "cafebabe" in result.detail

    def test_stale_checks_only_concluded_runs(self):
        """An in-progress run on an old commit is still 'in_progress', not stale."""
        runs = [_run(9, "in_progress", None, headSha="deadbeef")]
        result = branch_ci_conclusion(
            "/repo", "agent-a", head_sha="cafebabe",
            run_gh=lambda *a, **k: _run_json(runs),
        )
        assert result.state == "in_progress"

    def test_in_progress_when_run_queued(self):
        runs = [_run(9, "queued", None)]
        result = branch_ci_conclusion("/repo", "agent-a", run_gh=lambda *a, **k: _run_json(runs))
        assert result.state == "in_progress"

    def test_not_run_when_no_runs(self):
        """CI has never run on the branch — a distinct state from 'pass'."""
        result = branch_ci_conclusion("/repo", "agent-a", run_gh=lambda *a, **k: _run_json([]))
        assert result.state == "not_run"
        assert "never run" in result.detail

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

    def test_queries_branch_workflow_and_context_fields(self):
        captured = {}

        def fake_gh(args, cwd):
            captured["args"] = args
            captured["cwd"] = cwd
            return _run_json([_run(1, "completed", "success")])

        branch_ci_conclusion("/repo", "feature-x", run_gh=fake_gh)
        assert "--branch" in captured["args"]
        assert "feature-x" in captured["args"]
        assert "--workflow" in captured["args"]
        assert "ci.yml" in captured["args"]
        json_fields = captured["args"][captured["args"].index("--json") + 1]
        assert "headSha" in json_fields
        assert "updatedAt" in json_fields
        assert captured["cwd"] == "/repo"


class TestWaitForCIConclusion:
    def test_returns_terminal_conclusion_immediately(self):
        runs = [_run(42, "completed", "success")]
        result = wait_for_ci_conclusion(
            "/repo", "agent-a", run_gh=lambda *a, **k: _run_json(runs),
            sleep_fn=lambda _: (_ for _ in ()).throw(AssertionError("should not sleep")),
        )
        assert result.state == "pass"

    def test_polls_through_not_run_and_in_progress(self):
        """Right after a push 'not run' is a race, not a verdict — poll on."""
        sequence = [
            _run_json([]),                                     # not_run yet
            _run_json([_run(9, "in_progress", None)]),         # queued/in progress
            _run_json([_run(42, "completed", "success")]),     # concluded
        ]

        def fake_gh(args, cwd):
            return sequence.pop(0)

        seen = []
        wait_for_ci_conclusion(
            "/repo", "a",
            run_gh=fake_gh,
            sleep_fn=lambda _: seen.append("slept"),
        )
        assert seen == ["slept", "slept"]  # two waits, no real time passed

    def test_times_out_when_never_concludes(self):
        runs = _run_json([_run(9, "in_progress", None)])

        with pytest.raises(CIGateError, match="Timed out"):
            wait_for_ci_conclusion(
                "/repo", "a",
                timeout=0.0, poll_interval=0.001,
                run_gh=lambda *a, **k: runs,
                sleep_fn=lambda _: None,
            )

    def test_progress_callback_receives_waiting_state(self):
        runs = _run_json([_run(9, "in_progress", None)])

        progress_seen = []

        def fake_progress(waiting, remaining):
            progress_seen.append((waiting.state, remaining))

        with pytest.raises(CIGateError, match="Timed out"):
            wait_for_ci_conclusion(
                "/repo", "a",
                timeout=0.1, poll_interval=0.001,
                run_gh=lambda *a, **k: runs,
                sleep_fn=lambda _: None,
                progress=fake_progress,
            )
        assert progress_seen, "progress was never called"
        assert all(state == "in_progress" for state, _ in progress_seen)
