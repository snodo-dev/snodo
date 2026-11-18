import json
from types import SimpleNamespace

from snodo.survey import adr_analysis


def _proposal():
    return {
        "title": "Consistent pattern", "context": "Observed in the implementation.",
        "decision": "The code follows this pattern.", "consequences": "Changes follow it.",
        "evidence": [{"path": f"src/{i}.py"} for i in range(3)],
    }


def _setup(monkeypatch, root, models):
    from snodo import config
    from snodo import recon
    (root / "docs/decisions").mkdir(parents=True)
    (root / "docs/decisions/001-example.md").write_text("# Existing decision\n")
    (root / "src").mkdir()
    for i in range(3):
        (root / f"src/{i}.py").write_text("x = 1\n")
    monkeypatch.setattr(config.ConfigManager, "load", lambda self: {"llm": {"recon": {"models": models}}})
    monkeypatch.setattr(recon, "resolve_agent_model", lambda model: "resolved-default" if model == "default" else model)
    monkeypatch.setattr(adr_analysis, "_existing_titles", adr_analysis._existing_titles)


def test_first_model_prompt_and_good_answer(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path, ["first", "second"])
    observed = {}
    def fake(root, models, prompt, paths, label, turns, tokens):
        observed.update(models=models, prompt=prompt)
        return SimpleNamespace(result=json.dumps([_proposal()]), error=None)
    proposals, dropped, error = adr_analysis.analyze_adr_proposals(tmp_path, call=fake)
    assert observed["models"] == ["first"]
    assert "Existing decision" in observed["prompt"]
    assert len(proposals) == 1 and not dropped and error is None


def test_empty_model_list_uses_default(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path, [])
    seen = []
    def fake(*args):
        seen.extend(args[1])
        return SimpleNamespace(result="[]", error=None)
    assert adr_analysis.analyze_adr_proposals(tmp_path, call=fake)[2] is None
    assert seen == ["resolved-default"]


def test_errors_and_invalid_items(tmp_path, monkeypatch):
    _setup(monkeypatch, tmp_path, ["first"])
    failed = adr_analysis.analyze_adr_proposals(
        tmp_path, call=lambda *args: SimpleNamespace(result="", error="provider unavailable"),
    )
    assert failed[0] == [] and failed[2]
    answer = json.dumps([_proposal(), {**_proposal(), "evidence": [{"path": "../escape"}]}])
    proposals, dropped, error = adr_analysis.analyze_adr_proposals(
        tmp_path, call=lambda *args: SimpleNamespace(result=answer, error=None),
    )
    assert len(proposals) == 1 and dropped and error is None


def test_timeout_returns_error_without_proposals(tmp_path, monkeypatch):
    from concurrent.futures import TimeoutError

    _setup(monkeypatch, tmp_path, ["first"])
    from snodo import recon_settings
    monkeypatch.setattr(recon_settings, "resolve_recon_settings", lambda config: {
        "max_tool_turns": 40, "max_tokens": 16000, "deadline_seconds": 1,
    })

    class TimedOutFuture:
        def result(self, timeout):
            raise TimeoutError
        def cancel(self):
            return True

    class TimedOutExecutor:
        def __init__(self, **kwargs):
            pass
        def submit(self, *args):
            return TimedOutFuture()
        def shutdown(self, **kwargs):
            pass

    monkeypatch.setattr(adr_analysis, "ThreadPoolExecutor", TimedOutExecutor)
    proposals, dropped, error = adr_analysis.analyze_adr_proposals(
        tmp_path, call=lambda *args: None,
    )
    assert proposals == [] and dropped == []
    assert "timed out" in error
