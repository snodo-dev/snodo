"""Tests for OpenCodeCLIAdapter.

FILE: tests/coders/test_opencode_cli_adapter.py

Covers:
- workspace/workspace_mcp param resolution
- bare model extraction (opencode-cli/ prefix stripping)
- git-diff readback
- CodeArtifact building
- subprocess invocation with error handling
- adapter registration
"""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, call

import pytest

from snodo.core.interfaces import TaskSpec
from snodo.coders.opencode_cli_adapter import OpenCodeCLIAdapter
from snodo.tools.workspace import WorkspaceMCP


# ========== WORKSPACE PARAM RESOLUTION ==========

class TestWorkspaceResolution:
    """OpenCodeCLIAdapter accepts workspace from multiple sources."""

    def test_defaults_to_cwd(self):
        adapter = OpenCodeCLIAdapter(model="opencode-cli/test")
        assert adapter._workspace == Path.cwd()

    def test_workspace_param_takes_priority(self):
        adapter = OpenCodeCLIAdapter(
            model="opencode-cli/test",
            workspace=Path("/custom/workspace"),
        )
        assert adapter._workspace == Path("/custom/workspace")

    def test_workspace_mcp_with_project_root(self):
        workspace_mcp = Mock(spec=WorkspaceMCP)
        workspace_mcp.project_root = Path("/mcp/root")
        adapter = OpenCodeCLIAdapter(
            model="opencode-cli/test",
            workspace_mcp=workspace_mcp,
        )
        assert adapter._workspace == Path("/mcp/root")

    def test_workspace_overrides_workspace_mcp(self):
        workspace_mcp = Mock(spec=WorkspaceMCP)
        workspace_mcp.project_root = Path("/mcp/root")
        adapter = OpenCodeCLIAdapter(
            model="opencode-cli/test",
            workspace=Path("/explicit"),
            workspace_mcp=workspace_mcp,
        )
        assert adapter._workspace == Path("/explicit")


# ========== BARE MODEL ==========

class TestBareModel:
    """_bare_model strips the opencode-cli/ prefix."""

    def test_strips_prefix(self):
        adapter = OpenCodeCLIAdapter(model="opencode-cli/deepseek/deepseek-chat")
        assert adapter._bare_model() == "deepseek/deepseek-chat"

    def test_no_prefix_passthrough(self):
        adapter = OpenCodeCLIAdapter(model="deepseek/deepseek-chat")
        assert adapter._bare_model() == "deepseek/deepseek-chat"


# ========== GIT-DIFF READBACK ==========

class TestGitReadback:
    """_read_changes_from_disk detects file changes via git."""

    @pytest.fixture
    def git_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=tmpdir, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=tmpdir, check=True, capture_output=True,
            )
            readme = Path(tmpdir) / "README.md"
            readme.write_text("# Initial")
            subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=tmpdir, check=True, capture_output=True,
            )
            yield Path(tmpdir)

    def test_modified_file(self, git_workspace):
        (git_workspace / "README.md").write_text("# Modified")
        adapter = OpenCodeCLIAdapter(model="opencode-cli/test", workspace=git_workspace)
        entries = adapter._read_changes_from_disk()
        assert len(entries) == 1
        assert entries[0]["file"] == "README.md"

    def test_new_file(self, git_workspace):
        (git_workspace / "src" / "main.py").parent.mkdir(parents=True, exist_ok=True)
        (git_workspace / "src" / "main.py").write_text("print('hello')")
        adapter = OpenCodeCLIAdapter(model="opencode-cli/test", workspace=git_workspace)
        entries = adapter._read_changes_from_disk()
        paths = [e["file"] for e in entries]
        assert "src/main.py" in paths

    def test_deleted_file(self, git_workspace):
        (git_workspace / "README.md").unlink()
        adapter = OpenCodeCLIAdapter(model="opencode-cli/test", workspace=git_workspace)
        entries = adapter._read_changes_from_disk()
        assert any(e["file"] == "README.md" and e["status"] == "deleted" for e in entries)

    def test_empty_repo_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = OpenCodeCLIAdapter(model="opencode-cli/test", workspace=Path(tmpdir))
            entries = adapter._read_changes_from_disk()
            assert entries == []


# ========== DIFF-TO-ARTIFACT ==========

class TestDiffToArtifact:
    """_diff_to_artifact re-reads on-disk content."""

    def test_reads_file_from_disk(self, tmp_path):
        (tmp_path / "src" / "main.py").parent.mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("print('hello')")
        adapter = OpenCodeCLIAdapter(model="opencode-cli/test", workspace=tmp_path)
        artifact = adapter._diff_to_artifact([
            {"file": "src/main.py", "status": "modified"},
        ])
        assert len(artifact.files) == 1
        assert artifact.files[0].path == "src/main.py"
        assert artifact.files[0].content == "print('hello')"
        assert artifact.files[0].action == "write"

    def test_deleted_file(self, tmp_path):
        (tmp_path / "old.py").write_text("old")
        adapter = OpenCodeCLIAdapter(model="opencode-cli/test", workspace=tmp_path)
        artifact = adapter._diff_to_artifact([
            {"file": "old.py", "status": "deleted"},
        ])
        assert len(artifact.files) == 1
        assert artifact.files[0].content == ""
        assert artifact.files[0].action == "delete"

    def test_empty_entries_warns(self, tmp_path):
        adapter = OpenCodeCLIAdapter(model="opencode-cli/test", workspace=tmp_path)
        artifact = adapter._diff_to_artifact([])
        assert len(artifact.files) == 0


# ========== SUBPROCESS INVOCATION ==========

class TestSubprocessInvocation:
    """implement() shells opencode run with the right args."""

    @pytest.fixture
    def git_workspace(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=tmpdir, check=True, capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=tmpdir, check=True, capture_output=True,
            )
            readme = Path(tmpdir) / "README.md"
            readme.write_text("# Initial")
            subprocess.run(["git", "add", "."], cwd=tmpdir, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=tmpdir, check=True, capture_output=True,
            )
            yield Path(tmpdir)

    def test_invokes_with_correct_args(self, git_workspace):
        adapter = OpenCodeCLIAdapter(
            model="opencode-cli/deepseek/deepseek-chat",
            workspace=git_workspace,
        )
        spec = TaskSpec(description="add feature", constraints=[])

        with patch.object(adapter, "_build_prompt", return_value="test prompt"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = ""
                mock_run.return_value.stderr = ""

                adapter.implement(spec)

                call_args = mock_run.call_args[0][0]
                assert call_args[0] == "opencode"
                assert call_args[1] == "run"
                assert call_args[2] == "--dir"
                assert call_args[3] == str(git_workspace)
                assert call_args[4] == "--dangerously-skip-permissions"
                assert call_args[5] == "test prompt"
                assert call_args[6] == "-m"
                assert call_args[7] == "deepseek/deepseek-chat"

    def test_opencode_not_found_raises(self, git_workspace):
        adapter = OpenCodeCLIAdapter(
            model="opencode-cli/deepseek/deepseek-chat",
            workspace=git_workspace,
        )
        spec = TaskSpec(description="test", constraints=[])

        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(Exception, match="opencode not found"):
                adapter.implement(spec)

    def test_non_zero_exit_raises(self, git_workspace):
        adapter = OpenCodeCLIAdapter(
            model="opencode-cli/test",
            workspace=git_workspace,
        )
        spec = TaskSpec(description="test", constraints=[])

        with patch("subprocess.run") as mock_run:
            mock_run.return_value.returncode = 1
            mock_run.return_value.stderr = "something went wrong"
            mock_run.return_value.stdout = ""

            with pytest.raises(Exception, match="opencode run failed"):
                adapter.implement(spec)


# ========== ADAPTER REGISTRATION ==========

class TestRegistration:
    """OpenCodeCLIAdapter is registered in the coder registry."""

    def test_registry_contains_opencode_cli(self):
        from snodo.coders import CODER_REGISTRY
        assert "opencode-cli" in CODER_REGISTRY

    def test_resolve_adapter_class_routes_opencode_cli(self):
        from snodo.coders import resolve_adapter_class
        cls = resolve_adapter_class("opencode-cli/deepseek/deepseek-chat")
        assert cls is OpenCodeCLIAdapter

    def test_container_opencode_still_routes_to_container_adapter(self):
        from snodo.coders import resolve_adapter_class
        from snodo.coders.opencode_adapter import OpenCodeAdapter
        cls = resolve_adapter_class("opencode/deepseek/deepseek-chat")
        assert cls is OpenCodeAdapter

    def test_get_coder_opencode_cli(self):
        from snodo.coders import get_coder
        coder = get_coder("opencode-cli", model="opencode-cli/deepseek/deepseek-chat")
        assert isinstance(coder, OpenCodeCLIAdapter)
        assert coder.model == "opencode-cli/deepseek/deepseek-chat"
