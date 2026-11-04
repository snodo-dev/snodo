"""Tests for the benchmark harness preflight notice."""

from snodo.cli.commands.models_benchmark_notice import print_benchmark_notice


def _notice(tmp_path, baseline, model, monkeypatch, capsys):
    config = tmp_path / ".snodo"
    config.mkdir(exist_ok=True)
    (config / "protocol.yml").write_text("modes:\n  - mode_id: build\n    coder: litellm\n", encoding="utf-8")
    print_benchmark_notice(tmp_path, baseline, model)
    return capsys.readouterr().err


def test_notice_same_coder(tmp_path, capsys, monkeypatch):
    output = _notice(tmp_path, {"coder": "litellm", "model": "old"}, "openai/new", monkeypatch, capsys)
    assert "Baseline: coder litellm; model old" in output
    assert "Candidate: coder litellm; model openai/new" in output
    assert "Harness differs" not in output


def test_notice_different_coders(tmp_path, capsys, monkeypatch):
    output = _notice(tmp_path, {"coder": "opencode-cli", "model": "old"}, "gpu2/new", monkeypatch, capsys)
    assert "Harness differs" in output
    assert "opencode-cli" in output and "litellm" in output


def test_notice_missing_baseline_coder_is_unknown(tmp_path, capsys, monkeypatch):
    output = _notice(tmp_path, {"model": "old"}, "new", monkeypatch, capsys)
    assert "Baseline: coder unknown; model old" in output
    assert "Candidate: coder litellm; model new" in output


def test_notice_candidate_model_provider_prefix_forms(tmp_path, capsys, monkeypatch):
    prefixed = _notice(tmp_path, {}, "provider/model", monkeypatch, capsys)
    assert "model provider/model" in prefixed


def test_notice_candidate_without_configured_coder_uses_model_default(tmp_path, capsys):
    config = tmp_path / ".snodo"
    config.mkdir()
    (config / "protocol.yml").write_text("modes: []\n", encoding="utf-8")

    print_benchmark_notice(tmp_path, {"coder": "litellm", "model": "old"}, "openai/new")

    output = capsys.readouterr().err
    assert "Candidate: coder litellm; model openai/new" in output
    assert "Harness differs" not in output


def test_notice_ignores_malformed_modes_and_reports_harness_difference(tmp_path, capsys):
    config = tmp_path / ".snodo"
    config.mkdir()
    (config / "protocol.yml").write_text("modes: [null, build]\n", encoding="utf-8")

    print_benchmark_notice(tmp_path, {"coder": "opencode-cli", "model": "old"}, "openai/new")

    output = capsys.readouterr().err
    assert "Candidate: coder litellm; model openai/new" in output
    assert "Harness differs: baseline ran in opencode-cli; candidate runs in litellm." in output
