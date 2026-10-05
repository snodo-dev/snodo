import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pr_review.py"
SPEC = importlib.util.spec_from_file_location("pr_review", SCRIPT)
review = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(review)


def _results():
    return [
        {"agent": "alpha", "result": "Finding: missing test at src/a.py:12"},
        {"agent": "beta", "result": "Finding: missing test at src/a.py:12"},
        {"agent": "gamma", "result": "No issues found"},
    ]


def _summary(outcome="MINOR REWORK"):
    return {"outcome": outcome, "summary": "Three reviewers returned.", "rows": [{"kind": "Agreement", "finding": "Add the missing test.", "agents": {"alpha": "Agree", "beta": "Agree", "gamma": "No"}, "status": "Open"}], "rework": ["Add regression coverage."]}


def test_run_recon_maps_manager_results(monkeypatch, tmp_path):
    import snodo.recon
    import snodo.infrastructure.session
    from snodo.recon import _active_session_id

    real_session_manager = snodo.infrastructure.session.SessionManager

    def isolated_session_manager():
        return real_session_manager(sessions_dir=tmp_path / "sessions")

    class FakeManager:
        def __init__(self, root):
            self.root = root
            self.session_id = _active_session_id(root)

        def submit(self, question, paths, agents):
            assert question == "review"
            assert paths == ["."]
            assert agents == [["model-a"], ["model-b"], ["model-c"]]
            assert self.session_id == "sess_ci_pr-review_12345_2"
            return "rec_test"

        def get_status(self, _recon_id):
            return {"status": "complete"}

        def get_results(self, _recon_id):
            return {"status": "complete", "results": _results()}

    monkeypatch.setattr(review, "_load_recon_models", lambda: ["model-a", "model-b", "model-c"])
    monkeypatch.setattr(snodo.recon, "ReconManager", FakeManager)
    monkeypatch.setattr(snodo.infrastructure.session, "SessionManager", isolated_session_manager)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    assert review.run_recon("review", "example/example", 1) == _results()
    assert _active_session_id(str(tmp_path)) == "sess_ci_pr-review_12345_2"
    assert (tmp_path / ".snodo" / "state.json").exists()


def test_run_recon_keeps_failed_agent_error(monkeypatch, tmp_path):
    import snodo.recon

    class FakeManager:
        def __init__(self, _root):
            pass

        def submit(self, *_args, **_kwargs):
            return "rec_test"

        def get_status(self, _recon_id):
            return {"status": "complete"}

        def get_results(self, _recon_id):
            return {"status": "complete", "results": [{"agent": "alpha", "result": "", "error": "provider failure"}]}

    monkeypatch.setattr(review, "_load_recon_models", lambda: ["model-a", "model-b", "model-c"])
    monkeypatch.setattr(snodo.recon, "ReconManager", FakeManager)
    # run_recon uses the current directory as its project root. Keep its
    # session/state writes in a temporary project rather than the suite checkout.
    monkeypatch.chdir(tmp_path)
    assert review.run_recon("review", "example/example", 1) == [
        {"agent": "alpha", "result": "", "error": "provider failure"}
    ]


def test_run_recon_timeout_returns_per_agent_errors(monkeypatch):
    import snodo.recon

    class FakeManager:
        def __init__(self, _root):
            pass

        def submit(self, *_args, **_kwargs):
            return "rec_test"

        def get_status(self, _recon_id):
            return {"status": "running"}

    monkeypatch.setattr(review, "_load_recon_models", lambda: ["model-a", "model-b", "model-c"])
    monkeypatch.setattr(snodo.recon, "ReconManager", FakeManager)
    monkeypatch.setattr(review, "RECON_TIMEOUT_SECONDS", 0)
    assert review.run_recon("review", "example/example", 1) == [
        {"agent": f"agent-{i}", "result": "", "error": "recon timed out"}
        for i in range(1, 4)
    ]


def test_dry_run_never_constructs_recon_manager(tmp_path, monkeypatch):
    import snodo.recon

    def fail_if_constructed(*_args, **_kwargs):
        raise AssertionError("dry run constructed ReconManager")

    monkeypatch.setattr(snodo.recon, "ReconManager", fail_if_constructed)
    source, output = tmp_path / "results.json", tmp_path / "out.md"
    source.write_text(json.dumps(_results()))
    assert review.main(["--pr", "1", "--repo", "example/example", "--out", str(output), "--dry-run-results", str(source)]) == 0
    assert output.read_text().startswith("## Suggested outcome: ")


def test_dry_run_renders_expected_comment(tmp_path, monkeypatch):
    source, output = tmp_path / "results.json", tmp_path / "out.md"
    source.write_text(json.dumps(_results()))
    monkeypatch.setattr(review, "synthesize", lambda _results: _summary())
    assert review.main(["--pr", "1", "--repo", "example/example", "--out", str(output), "--dry-run-results", str(source)]) == 0
    text = output.read_text()
    assert text.startswith("## Suggested outcome: MINOR REWORK\n")
    assert "| Kind | Finding | alpha | beta | gamma | Status |" in text
    assert "<details><summary>alpha</summary>" in text


def test_synthesis_validates_and_retries_once(monkeypatch):
    answers = iter(['{"outcome":"maybe"}', json.dumps(_summary("REJECTED"))])
    monkeypatch.setattr(review, "_synthesize", lambda _results: next(answers))
    assert review.synthesize(_results())["outcome"] == "REJECTED"


def test_synthesis_fails_after_second_invalid_label(monkeypatch):
    monkeypatch.setattr(review, "_synthesize", lambda _results: '{"outcome":"unknown"}')
    import pytest
    with pytest.raises(ValueError, match="outcome"):
        review.synthesize(_results())


def test_fewer_than_two_agents_fails_without_output(tmp_path, monkeypatch):
    source, output = tmp_path / "results.json", tmp_path / "out.md"
    source.write_text(json.dumps(_results()[:1]))
    monkeypatch.setattr(review, "synthesize", lambda _results: _summary())
    assert review.main(["--pr", "1", "--repo", "example/example", "--out", str(output), "--dry-run-results", str(source)]) != 0
    assert not output.exists()


def test_diff_truncation_is_visible_in_question_and_comment():
    question, truncated = review.build_question("title", "body", "x" * 12, 5)
    assert truncated and "DIFF TRUNCATED" in question
    comment = review.render_comment(_results(), _summary(), truncated=True)
    assert "diff was truncated" in comment


def test_environment_secrets_are_redacted_from_output(monkeypatch):
    monkeypatch.setenv("EXAMPLE_API_KEY", "super-secret-token")
    rendered = review._redact_environment("provider said super-secret-token")
    assert "super-secret-token" not in rendered
    assert "[REDACTED]" in rendered


def test_empty_agent_remains_in_details_but_not_table_columns():
    results = _results() + [{"agent": "delta", "result": "", "error": "provider failure"}]
    comment = review.render_comment(results, _summary())
    assert "| Kind | Finding | alpha | beta | gamma | Status |" in comment
    assert "Failed or empty: delta." in comment
    assert "<details><summary>delta</summary>" in comment
    assert "Error: provider failure" in comment


def test_fix_forward_issue_body_for_each_non_merged_outcome():
    for outcome in ("MINOR REWORK", "MAJOR REWORK", "REJECTED"):
        body = review.render_fix_forward_issue(
            "snodo-dev/snodo", 42, "Review title", "https://example.test/review", _summary(outcome)
        )
        assert f"## Review outcome: {outcome}" in body
        assert "https://github.com/snodo-dev/snodo/pull/42" in body
        assert "https://example.test/review" in body
        assert "Add the missing test." in body
        assert "Add regression coverage." in body
        assert "tracking issue" in body
        assert "Fixes #<this issue's number>" in body


def test_merged_outcome_does_not_write_issue_body(tmp_path, monkeypatch):
    source, output, issue_body = tmp_path / "results.json", tmp_path / "out.md", tmp_path / "issue.md"
    source.write_text(json.dumps({"results": _results(), "synthesis": _summary("MERGED")}))
    assert review.main([
        "--pr", "1", "--repo", "example/example", "--out", str(output),
        "--issue-body", str(issue_body), "--dry-run-results", str(source),
    ]) == 0
    assert not issue_body.exists()


def test_fix_forward_body_redacts_environment_secrets(tmp_path, monkeypatch):
    secret = "hidden-credential-value"
    monkeypatch.setenv("EXAMPLE_API_KEY", secret)
    source, output, issue_body = tmp_path / "results.json", tmp_path / "out.md", tmp_path / "issue.md"
    synthesis = _summary()
    synthesis["summary"] = f"Provider leaked {secret}"
    source.write_text(json.dumps({"results": _results(), "synthesis": synthesis}))
    assert review.main([
        "--pr", "1", "--repo", "example/example", "--out", str(output),
        "--issue-body", str(issue_body), "--dry-run-results", str(source),
    ]) == 0
    assert secret not in issue_body.read_text()
    assert "[REDACTED]" in issue_body.read_text()


def test_fix_forward_body_failure_keeps_review_comment_and_success(tmp_path, monkeypatch, capsys):
    source, output, issue_body = tmp_path / "results.json", tmp_path / "out.md", tmp_path / "issue.md"
    source.write_text(json.dumps({"results": _results(), "synthesis": _summary()}))

    def fail_issue_body(*_args, **_kwargs):
        raise RuntimeError("title unavailable")

    monkeypatch.setattr(review, "render_fix_forward_issue", fail_issue_body)
    result = review.main([
        "--pr", "1", "--repo", "example/example", "--out", str(output),
        "--issue-body", str(issue_body), "--dry-run-results", str(source),
    ])

    assert result == 0
    assert output.read_text().startswith("## Suggested outcome: MINOR REWORK\n")
    assert not issue_body.exists()
    assert "error building fix-forward issue body: title unavailable" in capsys.readouterr().err
