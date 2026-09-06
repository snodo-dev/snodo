"""Tests for repository-local snodo home exclusion and protection by coder adapters.

FILE: tests/coders/test_local_home_protection.py (Fixes #227)
"""

from pathlib import Path

import pytest
from git import Repo

from snodo.coders.base import InPlaceCoderAdapter
from snodo.core.interfaces import CodeArtifact, TaskSpec


class _DummyInPlaceCoder(InPlaceCoderAdapter):
    coder_name = "dummy-coder"

    def __init__(self, workspace: Path):
        self._workspace = workspace
        self.last_commit_reason = None
        self._head_before_run = None

    def _implement_in_place(self, spec: TaskSpec) -> CodeArtifact:
        (self._workspace / "src" / "app.py").write_text("print('hello')\n")
        return CodeArtifact(files=[])


@pytest.fixture
def git_project_with_local_home(tmp_path, monkeypatch):
    """Create a git repo with a repository-local SNODO_HOME."""
    repo_dir = tmp_path / "my_project"
    repo_dir.mkdir()
    (repo_dir / "src").mkdir()
    (repo_dir / "src" / "app.py").write_text("print('initial')\n")
    (repo_dir / ".gitignore").write_text(".snodo/\n.custom-home/\n")

    repo = Repo.init(str(repo_dir))
    repo.git.config("user.name", "Test User")
    repo.git.config("user.email", "test@snodo.exp")
    repo.git.add("-A")
    repo.git.commit("-m", "initial commit")

    local_home = repo_dir / ".custom-home"
    local_home.mkdir()
    (local_home / "config.yml").write_text("providers:\n  anthropic:\n    api_key: 'test'\n")
    (local_home / "tokens.db").write_text("binary token data")

    monkeypatch.setenv("SNODO_HOME", str(local_home))
    return repo_dir, local_home


def test_coder_staging_and_readback_excludes_repository_local_home(git_project_with_local_home):
    """In-place coder commits code changes but strictly excludes files in repository-local home (Fixes #227)."""
    repo_dir, local_home = git_project_with_local_home

    coder = _DummyInPlaceCoder(repo_dir)
    # Modify code file and touch a file in local home
    (repo_dir / "src" / "app.py").write_text("print('updated app')\n")
    (local_home / "new_credentials.json").write_text('{"secret": "key"}')

    # Commit changes via coder adapter
    coder._commit_changes()

    repo = Repo(str(repo_dir))
    committed_files = [item.path for item in repo.head.commit.tree.traverse() if item.type == "blob"]

    # src/app.py is committed
    assert "src/app.py" in committed_files
    # Local home files are NOT committed
    assert not any(f.startswith(".custom-home") for f in committed_files)

    # Git readback only sees code changes
    changed = coder._read_changes_from_disk()
    files = [c["file"] for c in changed]
    assert "src/app.py" in files
    assert not any(f.startswith(".custom-home") for f in files)

