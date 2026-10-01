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
