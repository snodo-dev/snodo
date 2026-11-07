"""Tests for the demo workspace helpers."""

import importlib.util
import json
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "demo_workspace", ROOT / "demo" / "workspace.py"
)
demo_workspace = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo_workspace)


def test_load_instance_reads_required_keys(tmp_path):
    instance = {
        "instance_id": "demo-1",
        "repo_url": "https://example.invalid/repo.git",
        "base_commit": "abc123",
    }
    path = tmp_path / "instance.json"
    path.write_text(json.dumps(instance), encoding="utf-8")
    assert demo_workspace.load_instance(path) == instance


def test_load_instance_names_the_missing_key(tmp_path):
    path = tmp_path / "instance.json"
    path.write_text(json.dumps({"instance_id": "demo-1"}), encoding="utf-8")
    with pytest.raises(ValueError, match="repo_url"):
        demo_workspace.load_instance(path)


def test_clone_checks_out_base_commit(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(source), "config", "user.name", "Test"], check=True
    )
    tracked = source / "file.txt"
    tracked.write_text("first\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "file.txt"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "-m", "first"], check=True)
    first_commit = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tracked.write_text("second\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "commit", "-am", "second"], check=True)

    target = tmp_path / "clone"
    instance = {"repo_url": str(source), "base_commit": first_commit}
    demo_workspace.clone_instance(instance, target)
    assert (target / "file.txt").read_text(encoding="utf-8") == "first\n"
    head = subprocess.run(
        ["git", "-C", str(target), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == first_commit
    branch = subprocess.run(
        ["git", "-C", str(target), "branch", "--show-current"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert branch == ""


def test_non_empty_target_is_refused(tmp_path):
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError, match="not empty"):
        demo_workspace.clone_instance(
            {"repo_url": "unused", "base_commit": "unused"},
            target,
            runner=lambda *args, **kwargs: pytest.fail("runner should not be called"),
        )


def test_init_command_uses_bugfix_surgeon_template(tmp_path):
    calls = []

    def recording_runner(command, **kwargs):
        calls.append((command, kwargs))

    demo_workspace.init_workspace(tmp_path, ["snodo"], runner=recording_runner)
    assert calls == [
        (
            ["snodo", "init", "--template", "bugfix-surgeon", "--yes"],
            {"cwd": tmp_path, "check": True},
        )
    ]
