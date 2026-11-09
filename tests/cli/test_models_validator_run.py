"""Tests for recorded-tree validator replay."""

from types import SimpleNamespace


def test_replay_judges_the_recorded_commit_not_the_checkout(tmp_path, monkeypatch):
    from snodo.cli.commands import models_validator_run as replay

    observed = {}
    monkeypatch.setattr(replay, "resolve_validators", lambda *a: (object(), [SimpleNamespace(validator_id="v", model=None, model_copy=lambda **kw: SimpleNamespace(validator_id="v"))]))

    # The real integration test below exercises worktree lifecycle; this test
    # verifies explicit recorded refs reach the shared runner.
    def fake_runner(**kwargs):
        observed["args"] = kwargs
        observed["recorded_content"] = (kwargs["workspace_mcp"].project_root / "tracked").read_text()
        return [], {}

    monkeypatch.setattr(replay, "run_validators", fake_runner)
    from git import Repo
    root = tmp_path / "repo"
    root.mkdir()
    repo = Repo.init(root)
    (root / "tracked").write_text("recorded")
    repo.index.add(["tracked"])
    recorded = repo.index.commit("record")
    sha = recorded.hexsha
    (root / "tracked").write_text("checkout change")
    result = replay.replay_validator(root, object(), "m", "v", "pre_execute", "spec", sha, sha, "fake/model", completion_fn=lambda: None, validator_config=SimpleNamespace(max_tokens=1, max_tool_turns=1))
    assert result["commit"] == sha
    assert observed["args"]["base_ref"] == sha
    assert observed["args"]["workspace_mcp"].project_root != str(root)
    assert observed["recorded_content"] == "recorded"
    repo.close()


def test_replay_runs_only_the_named_validator(tmp_path, monkeypatch):
    from snodo.cli.commands import models_validator_run as replay
    validator = SimpleNamespace(validator_id="chosen", model="pinned", model_copy=lambda **kw: validator)
    monkeypatch.setattr(replay, "resolve_validators", lambda *a: (object(), [validator, SimpleNamespace(validator_id="other", model=None)]))
    seen = {}
    monkeypatch.setattr(replay, "run_validators", lambda **kw: (seen.setdefault("validators", kw["validators"]) or [], {}))
    from git import Repo
    root = tmp_path / "repo"
    root.mkdir()
    repo = Repo.init(root)
    (root / "f").write_text("x")
    repo.index.add(["f"])
    sha = repo.index.commit("x").hexsha
    replay.replay_validator(root, object(), "m", "chosen", "pre_execute", "s", sha, sha, "chosen/model", lambda: None, SimpleNamespace(max_tokens=1, max_tool_turns=1))
    assert [v.validator_id for v in seen["validators"]] == ["chosen"]


def test_replay_uses_no_verdict_cache(tmp_path, monkeypatch):
    from snodo.cli.commands import models_validator_run as replay
    validator = SimpleNamespace(validator_id="v", model=None, model_copy=lambda **kw: validator)
    monkeypatch.setattr(replay, "resolve_validators", lambda *a: (object(), [validator]))
    seen = {}
    monkeypatch.setattr(replay, "run_validators", lambda **kw: (seen.update(kw) or [], {}))
    from git import Repo
    root = tmp_path / "repo"
    root.mkdir()
    repo = Repo.init(root)
    (root / "f").write_text("x")
    repo.index.add(["f"])
    sha = repo.index.commit("x").hexsha
    replay.replay_validator(root, object(), "m", "v", "pre_execute", "s", sha, sha, "m", lambda: None, SimpleNamespace(max_tokens=1, max_tool_turns=1))
    assert seen["verdict_cache"] is None


def test_replay_removes_its_worktree_after_a_failure(tmp_path, monkeypatch):
    from snodo.cli.commands import models_validator_run as replay
    validator = SimpleNamespace(validator_id="v", model=None, model_copy=lambda **kw: validator)
    monkeypatch.setattr(replay, "resolve_validators", lambda *a: (object(), [validator]))
    monkeypatch.setattr(replay, "run_validators", lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    from git import Repo
    root = tmp_path / "repo"
    root.mkdir()
    repo = Repo.init(root)
    (root / "f").write_text("x")
    repo.index.add(["f"])
    sha = repo.index.commit("x").hexsha
    result = replay.replay_validator(root, object(), "m", "v", "pre_execute", "s", sha, sha, "m", lambda: None, SimpleNamespace(max_tokens=1, max_tool_turns=1))
    assert result["error"] is True
    assert not [w for w in repo.git.worktree("list", "--porcelain").split("worktree ")[2:]]


def test_replay_reports_elapsed_time_and_tokens(tmp_path, monkeypatch):
    from snodo.cli.commands import models_validator_run as replay
    validator = SimpleNamespace(validator_id="v", model=None, model_copy=lambda **kw: validator)
    monkeypatch.setattr(replay, "resolve_validators", lambda *a: (object(), [validator]))
    monkeypatch.setattr(replay, "run_validators", lambda **kw: ([], {}))
    from git import Repo
    root = tmp_path / "repo"
    root.mkdir()
    repo = Repo.init(root)
    (root / "f").write_text("x")
    repo.index.add(["f"])
    sha = repo.index.commit("x").hexsha
    result = replay.replay_validator(root, object(), "m", "v", "pre_execute", "s", sha, sha, "m", lambda: None, SimpleNamespace(max_tokens=1, max_tool_turns=1))
    assert isinstance(result["elapsed_seconds"], float)
    assert result["input_tokens"] is None and result["output_tokens"] is None
