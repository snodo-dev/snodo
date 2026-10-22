"""Tests for HEAD-relative absent-claim detection."""

import subprocess
from pathlib import Path

from snodo.engine.absent_claims import detect_absent_claims


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _linked_workspace(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "project"
    root.mkdir()
    _git_init = subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "root-only.txt").write_text("root\n")
    _git(root, "add", "root-only.txt")
    _git(root, "commit", "-m", "initial")
    workspace = tmp_path / "workspace"
    _git(root, "worktree", "add", "-b", "dispatched", str(workspace))
    (workspace / "subject.py").write_text("# present in workspace HEAD\n")
    _git(workspace, "add", "subject.py")
    _git(workspace, "commit", "-m", "add workspace subject")
    assert not (root / "subject.py").exists()
    return root, workspace, _git(workspace, "rev-parse", "HEAD")


def test_detects_path_present_in_workspace_head_not_project_root(tmp_path: Path) -> None:
    _, workspace, head = _linked_workspace(tmp_path)

    result = detect_absent_claims("The requested `subject.py` is missing.", workspace)

    assert result is not None
    assert result.paths == ("subject.py",)
    assert result.head == head


def test_absent_at_head_is_not_a_contradiction(tmp_path: Path) -> None:
    _, workspace, _ = _linked_workspace(tmp_path)

    assert detect_absent_claims("`never-created.py` does not exist.", workspace) is None


def test_refusal_without_named_path_is_not_a_signal(tmp_path: Path) -> None:
    _, workspace, _ = _linked_workspace(tmp_path)

    assert detect_absent_claims("I cannot proceed because the files are unavailable.", workspace) is None


def test_path_escaping_workspace_is_ignored(tmp_path: Path) -> None:
    _, workspace, _ = _linked_workspace(tmp_path)

    assert detect_absent_claims("`../root-only.txt` is missing.", workspace) is None


def test_claim_requires_path_and_absence_in_same_role_and_sentence(tmp_path: Path) -> None:
    _, workspace, _ = _linked_workspace(tmp_path)

    assert detect_absent_claims("`subject.py` is missing.", workspace) is not None
    assert detect_absent_claims("I cannot find `subject.py`.", workspace) is not None
    assert detect_absent_claims(
        "The function is missing a docstring in `subject.py`.", workspace
    ) is None
    assert detect_absent_claims(
        "`subject.py` exists. The requested file is missing.", workspace
    ) is None
    assert detect_absent_claims(
        "The requested file is missing. `subject.py` exists.", workspace
    ) is None
