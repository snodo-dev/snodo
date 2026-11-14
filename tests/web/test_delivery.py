"""Tests for read-only plan delivery status."""

import json
import subprocess
from pathlib import Path

from snodo.web.delivery import IN_MAIN, NO_BRANCH, NOT_IN_MAIN, plan_delivery, plans_delivery


def git(path: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def repository(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.email", "test@example.com")
    git(path, "config", "user.name", "Test")
    (path / "file").write_text("base")
    git(path, "add", "file")
    git(path, "commit", "-m", "base")
    return path


def add_plan(path: Path, name: str, branch: str | None = None) -> None:
    folder = path / ".snodo" / "plans" / name
    folder.mkdir(parents=True)
    status = {"integration": {"branch": branch}} if branch else {}
    (folder / "status.json").write_text(json.dumps(status))


def commit_on_branch(path: Path, branch: str, message: str) -> None:
    git(path, "checkout", "-b", branch)
    (path / "file").write_text(message)
    git(path, "commit", "-am", message)


def snapshot(path: Path) -> tuple[str, str]:
    return git(path, "rev-parse", "HEAD"), git(path, "show-ref", "--head")


def test_merged_branch_is_in_main_and_reads_do_not_change_repo(tmp_path: Path) -> None:
    path = repository(tmp_path)
    commit_on_branch(path, "plan/merged", "merged")
    git(path, "checkout", "main")
    git(path, "merge", "--no-ff", "plan/merged", "-m", "merge")
    add_plan(path, "merged", "plan/merged")
    before = snapshot(path)

    assert plan_delivery(path, "merged") == IN_MAIN
    assert snapshot(path) == before


def test_branch_with_unmerged_extra_commit_is_not_in_main(tmp_path: Path) -> None:
    path = repository(tmp_path)
    commit_on_branch(path, "plan/pending", "pending")
    git(path, "checkout", "main")
    add_plan(path, "pending", "plan/pending")

    assert plan_delivery(path, "pending") == NOT_IN_MAIN


def test_missing_branch_and_status_return_no_branch(tmp_path: Path) -> None:
    path = repository(tmp_path)
    add_plan(path, "without-branch")
    (path / ".snodo" / "plans" / "without-status").mkdir()
    add_plan(path, "unknown", "missing")

    assert plan_delivery(path, "without-branch") == NO_BRANCH
    assert plan_delivery(path, "without-status") == NO_BRANCH
    assert plan_delivery(path / "not-a-repository", "unknown") == NO_BRANCH


def test_origin_only_branch_is_used(tmp_path: Path) -> None:
    path = repository(tmp_path)
    commit_on_branch(path, "plan/remote", "remote")
    tip = git(path, "rev-parse", "HEAD")
    git(path, "checkout", "main")
    git(path, "update-ref", "refs/remotes/origin/plan/remote", tip)
    add_plan(path, "remote", "plan/remote")

    assert plan_delivery(path, "remote") == NOT_IN_MAIN


def test_plans_delivery_includes_each_plan_directory(tmp_path: Path) -> None:
    path = repository(tmp_path)
    add_plan(path, "present", "absent")
    (path / ".snodo" / "plans" / "missing-status").mkdir()

    assert plans_delivery(path) == {"missing-status": NO_BRANCH, "present": NO_BRANCH}
