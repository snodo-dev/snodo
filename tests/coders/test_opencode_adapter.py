"""Tests for OpenCodeAdapter.

FILE: tests/coders/test_opencode_adapter.py

Covers:
- workspace/workspace_mcp param resolution
- model payload in message body
- git-diff readback from volume-mounted workspace
- fallback to /diff API
- Full implement flow with mocked HTTP + git
"""

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch, PropertyMock

import pytest
import httpx

from snodo.core.interfaces import TaskSpec, FileArtifact
from snodo.coders.opencode_adapter import OpenCodeAdapter
from snodo.tools.workspace import WorkspaceMCP


# ========== WORKSPACE PARAM RESOLUTION ==========

class TestWorkspaceResolution:
    """OpenCodeAdapter accepts workspace from multiple sources."""

    def test_defaults_to_cwd(self):
        adapter = OpenCodeAdapter(model="opencode/test")
        assert adapter._workspace == Path.cwd()

    def test_workspace_param_takes_priority(self):
        adapter = OpenCodeAdapter(
            model="opencode/test",
            workspace=Path("/custom/workspace"),
        )
        assert adapter._workspace == Path("/custom/workspace")

    def test_workspace_mcp_with_project_root(self):
        workspace_mcp = Mock(spec=WorkspaceMCP)
        workspace_mcp.project_root = Path("/mcp/root")
        adapter = OpenCodeAdapter(
            model="opencode/test",
            workspace_mcp=workspace_mcp,
        )
        assert adapter._workspace == Path("/mcp/root")

    def test_workspace_overrides_workspace_mcp(self):
        workspace_mcp = Mock(spec=WorkspaceMCP)
        workspace_mcp.project_root = Path("/mcp/root")
        adapter = OpenCodeAdapter(
            model="opencode/test",
            workspace=Path("/explicit"),
            workspace_mcp=workspace_mcp,
        )
        assert adapter._workspace == Path("/explicit")

    def test_non_workspace_mcp_object_falls_back_to_cwd(self):
        adapter = OpenCodeAdapter(
            model="opencode/test",
            workspace_mcp="not-a-WorkspaceMCP",
        )
        assert adapter._workspace == Path.cwd()


# ========== MODEL PAYLOAD ==========

class TestModelPayload:
    """Model payload is resolved and sent in the right places."""

    def test_resolve_model_payload_opencode_prefixed(self):
        adapter = OpenCodeAdapter(model="opencode/deepseek/deepseek-chat")
        payload = adapter._resolve_model_payload()
        assert payload == {"providerID": "deepseek", "modelID": "deepseek-chat"}

    def test_resolve_model_payload_opencode_no_provider(self):
        adapter = OpenCodeAdapter(model="opencode/gpt-4")
        payload = adapter._resolve_model_payload()
        assert payload == {"modelID": "gpt-4"}

    def test_resolve_model_payload_fallback(self):
        adapter = OpenCodeAdapter(model="claude-sonnet-4-20250514")
        payload = adapter._resolve_model_payload()
        assert payload == {"modelID": "claude-sonnet-4-20250514"}

    def test_message_body_includes_model(self):
        adapter = OpenCodeAdapter(model="opencode/deepseek/deepseek-chat")
        adapter._container = Mock()
        adapter._container.base_url = "http://localhost:55440"

        prompt_text = "test prompt"
        session_id = "test-session-1"
        with patch.object(adapter, "_build_prompt", return_value=prompt_text):
            with patch("httpx.post") as mock_post:
                mock_post.return_value.status_code = 200
                adapter._send_message(session_id, TaskSpec(description="test", constraints=[]))

                call_args = mock_post.call_args
                url = call_args[0][0]
                body = call_args[1]["json"]

                assert session_id in url
                assert body["model"] == {"providerID": "deepseek", "modelID": "deepseek-chat"}
                assert body["parts"] == [{"type": "text", "text": prompt_text}]

    def test_session_creation_also_has_model(self):
        adapter = OpenCodeAdapter(model="opencode/deepseek/deepseek-chat")
        adapter._container = Mock()
        adapter._container.base_url = "http://localhost:55440"

        with patch("httpx.post") as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"id": "session-abc"}
            mock_post.return_value = mock_response

            session_id = adapter._create_session()
            assert session_id == "session-abc"

            call_args = mock_post.call_args
            body = call_args[1]["json"]
            assert "model" in body


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
        adapter = OpenCodeAdapter(model="opencode/test", workspace=git_workspace)
        entries = adapter._read_changes_from_disk()
        assert len(entries) == 1
        assert entries[0]["file"] == "README.md"
        assert entries[0]["status"] in ("modified", "M")

    def test_new_file(self, git_workspace):
        (git_workspace / "src" / "main.py").parent.mkdir(parents=True, exist_ok=True)
        (git_workspace / "src" / "main.py").write_text("print('hello')")
        adapter = OpenCodeAdapter(model="opencode/test", workspace=git_workspace)
        entries = adapter._read_changes_from_disk()
        paths = [e["file"] for e in entries]
        assert "src/main.py" in paths

    def test_deleted_file(self, git_workspace):
        (git_workspace / "README.md").unlink()
        adapter = OpenCodeAdapter(model="opencode/test", workspace=git_workspace)
        entries = adapter._read_changes_from_disk()
        assert any(e["file"] == "README.md" and e["status"] == "deleted" for e in entries)

    def test_empty_repo_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            adapter = OpenCodeAdapter(model="opencode/test", workspace=Path(tmpdir))
            entries = adapter._read_changes_from_disk()
            assert entries == []

    def test_staged_changes_are_detected(self, git_workspace):
        (git_workspace / "staged.py").write_text("staged content")
        subprocess.run(
            ["git", "add", "staged.py"],
            cwd=str(git_workspace), check=True, capture_output=True,
        )
        adapter = OpenCodeAdapter(model="opencode/test", workspace=git_workspace)
        entries = adapter._read_changes_from_disk()
        assert any(e["file"] == "staged.py" for e in entries)


# ========== DIFF-TO-ARTIFACT ==========

class TestDiffToArtifact:
    """_diff_to_artifact re-reads on-disk content."""

    def test_reads_file_from_disk(self, tmp_path):
        (tmp_path / "src" / "main.py").parent.mkdir(parents=True)
        (tmp_path / "src" / "main.py").write_text("print('hello')")
        adapter = OpenCodeAdapter(model="opencode/test", workspace=tmp_path)
        artifact = adapter._diff_to_artifact([
            {"file": "src/main.py", "status": "modified"},
        ])
        assert len(artifact.files) == 1
        assert artifact.files[0].path == "src/main.py"
        assert artifact.files[0].content == "print('hello')"
        assert artifact.files[0].action == "write"

    def test_deleted_file(self, tmp_path):
        (tmp_path / "old.py").write_text("old")
        adapter = OpenCodeAdapter(model="opencode/test", workspace=tmp_path)
        artifact = adapter._diff_to_artifact([
            {"file": "old.py", "status": "deleted"},
        ])
        assert len(artifact.files) == 1
        assert artifact.files[0].path == "old.py"
        assert artifact.files[0].content == ""
        assert artifact.files[0].action == "delete"

    def test_empty_entries_warns(self, tmp_path):
        adapter = OpenCodeAdapter(model="opencode/test", workspace=tmp_path)
        artifact = adapter._diff_to_artifact([])
        assert len(artifact.files) == 0

    def test_skips_entry_without_file_key(self, tmp_path):
        adapter = OpenCodeAdapter(model="opencode/test", workspace=tmp_path)
        artifact = adapter._diff_to_artifact([
            {"status": "modified"},  # missing "file"
        ])
        assert len(artifact.files) == 0


# ========== IMPLEMENT FLOW ==========

class TestImplementFlow:
    """Full implement() flow with mocked HTTP + git."""

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

    def test_git_readback_primary_api_fallback_unused(self, git_workspace):
        """When git readback finds changes, the /diff API is NOT called."""
        (git_workspace / "README.md").write_text("# Modified by opencode")
        (git_workspace / "new.py").write_text("print('new')")

        adapter = OpenCodeAdapter(model="opencode/test", workspace=git_workspace)
        container_mock = Mock()
        container_mock.is_running.return_value = True
        container_mock.base_url = "http://localhost:55440"
        adapter._container = container_mock

        with patch.object(adapter, "_create_session", return_value="session-1"):
            with patch.object(adapter, "_wait_for_completion"):
                with patch.object(adapter, "_fetch_diff") as mock_fetch:
                    mock_fetch.return_value = []

                    spec = TaskSpec(description="test", constraints=[])
                    artifact = adapter.implement(spec)

                    # git readback found files, so /diff API should NOT be called
                    mock_fetch.assert_not_called()
                    assert len(artifact.files) == 2

    def test_fallback_to_api_diff_when_git_empty(self, git_workspace):
        """When git readback is empty, /diff API is called as fallback."""
        adapter = OpenCodeAdapter(model="opencode/test", workspace=git_workspace)
        container_mock = Mock()
        container_mock.is_running.return_value = True
        container_mock.base_url = "http://localhost:55440"
        adapter._container = container_mock

        with patch.object(adapter, "_create_session", return_value="session-1"):
            with patch.object(adapter, "_wait_for_completion"):
                with patch.object(adapter, "_read_changes_from_disk", return_value=[]):
                    with patch.object(adapter, "_fetch_diff") as mock_fetch:
                        mock_fetch.return_value = [
                            {"file": "README.md", "status": "modified"},
                        ]

                        spec = TaskSpec(description="test", constraints=[])
                        artifact = adapter.implement(spec)

                        mock_fetch.assert_called_once_with("session-1")
                        assert len(artifact.files) == 1

    def test_both_empty_returns_empty_artifact(self, git_workspace):
        """When both git and API diff are empty, returns empty CodeArtifact."""
        adapter = OpenCodeAdapter(model="opencode/test", workspace=git_workspace)
        container_mock = Mock()
        container_mock.is_running.return_value = True
        container_mock.base_url = "http://localhost:55440"
        adapter._container = container_mock

        with patch.object(adapter, "_create_session", return_value="session-1"):
            with patch.object(adapter, "_wait_for_completion"):
                with patch.object(adapter, "_read_changes_from_disk", return_value=[]):
                    with patch.object(adapter, "_fetch_diff", return_value=[]):
                        spec = TaskSpec(description="test", constraints=[])
                        artifact = adapter.implement(spec)
                        assert len(artifact.files) == 0

    def test_container_started_if_not_running(self):
        """Container is started if not already running."""
        adapter = OpenCodeAdapter(model="opencode/test", workspace=Path("/tmp"))
        container_mock = Mock()
        container_mock.is_running.return_value = False
        container_mock.image_exists.return_value = True
        container_mock.is_available.return_value = True
        container_mock.base_url = "http://localhost:55440"
        adapter._container = container_mock

        with patch.object(adapter, "_create_session", return_value="session-1"):
            with patch.object(adapter, "_wait_for_completion"):
                with patch.object(adapter, "_read_changes_from_disk", return_value=[]):
                    with patch.object(adapter, "_fetch_diff", return_value=[]):
                        spec = TaskSpec(description="test", constraints=[])
                        adapter.implement(spec)
                        container_mock.start.assert_called_once()
