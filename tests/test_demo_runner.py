"""Hermetic tests for the demo command runner."""

import json
import subprocess

import pytest

from demo import run_demo


def _source_repo(path):
    path.mkdir()
    subprocess.run(["git", "init", str(path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(path), "config", "user.name", "Test"], check=True)
    (path / "source.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "base"],
        check=True,
        capture_output=True,
    )
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class FakeRunner:
    def __init__(self, fail=False):
        self.fail = fail
        self.clones = []

    def __call__(self, command, *, cwd=None, **kwargs):
        if command[:2] == ["git", "clone"]:
            dest = command[-1]
            subprocess.run(command, check=True, capture_output=True)
            self.clones.append(dest)
        elif command[:3] == ["git", "checkout", "--detach"]:
            subprocess.run(command, cwd=cwd, check=True, capture_output=True)
        elif command[:2] == ["snodo", "init"]:
            return subprocess.CompletedProcess(command, 0)
        elif "run" in command:
            tasks = cwd / ".snodo" / "tasks" / "demo-task"
            tasks.mkdir(parents=True)
            (tasks / "state.json").write_text(
                json.dumps({"status": "completed" if not self.fail else "failed"})
            )
            if self.fail:
                raise subprocess.CalledProcessError(1, command)
            return subprocess.CompletedProcess(command, 0)
        return subprocess.CompletedProcess(command, 0)


def _fixture(monkeypatch, source, commit):
    fixture = source.parent / "instance.json"
    fixture.write_text(
        json.dumps(
            {"instance_id": "test", "repo_url": str(source), "base_commit": commit}
        )
    )
    monkeypatch.setattr(run_demo, "FIXTURE", fixture)
    monkeypatch.setattr(
        run_demo.protocols, "governed_protocol_text", lambda: "governed"
    )
    monkeypatch.setattr(run_demo.protocols, "bare_protocol_text", lambda: "bare")
    monkeypatch.setattr(
        run_demo.protocols,
        "validate_protocol",
        lambda path: (None, {"unavailable": "test"}),
    )


def test_both_arms_get_separate_clones_at_base_commit(tmp_path, monkeypatch):
    source = tmp_path / "source"
    commit = _source_repo(source)
    _fixture(monkeypatch, source, commit)
    runner = FakeRunner()
    for arm in ("governed", "bare"):
        run_demo.run_arm("provider/model", arm, tmp_path / arm, runner=runner)
    assert len(set(runner.clones)) == 2
    assert all(
        subprocess.run(
            ["git", "-C", clone, "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        == commit
        for clone in runner.clones
    )


def test_each_arm_writes_record_and_report(tmp_path, monkeypatch):
    source = tmp_path / "source"
    commit = _source_repo(source)
    _fixture(monkeypatch, source, commit)
    run_demo.run_arm(
        "provider/model", "governed", tmp_path / "run", runner=FakeRunner()
    )
    assert (tmp_path / "run" / "governed.json").exists()
    assert (tmp_path / "run" / "governed.txt").exists()


def test_bare_arm_uses_bare_protocol_file(tmp_path, monkeypatch):
    source = tmp_path / "source"
    _fixture(monkeypatch, source, _source_repo(source))
    run_demo.run_arm("provider/model", "bare", tmp_path / "run", runner=FakeRunner())
    assert (tmp_path / "run" / "bare-protocol.yml").read_text() == "bare"


def test_failed_run_still_writes_record_not_passed(tmp_path, monkeypatch):
    source = tmp_path / "source"
    _fixture(monkeypatch, source, _source_repo(source))
    run_demo.run_arm(
        "provider/model", "governed", tmp_path / "run", runner=FakeRunner(fail=True)
    )
    result = json.loads((tmp_path / "run" / "governed.json").read_text())
    assert result["outcome"] != "passed"


def test_main_requires_model():
    with pytest.raises(SystemExit) as exc:
        run_demo.main([])
    assert exc.value.code == 2


def test_arm_option_runs_only_that_arm(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(
        run_demo,
        "run_arm",
        lambda model, arm, workdir, **kwargs: (calls.append(arm) or {}, "report"),
    )
    assert (
        run_demo.main(
            ["--model", "provider/model", "--arm", "bare", "--workdir", str(tmp_path)]
        )
        == 0
    )
    assert calls == ["bare"]
