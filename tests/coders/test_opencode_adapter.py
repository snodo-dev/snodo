"""Tests for OpenCodeAdapter.

FILE: tests/coders/test_opencode_adapter.py

Covers:
- workspace/workspace_mcp param resolution
- model payload in message body
- git-diff readback from volume-mounted workspace
- fallback to /diff API
- Full implement flow with mocked HTTP + git
"""

import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from snodo.coders.base import CoderUnavailableError, LLMCallError
from snodo.coders.opencode_adapter import OpenCodeAdapter
from snodo.core.interfaces import TaskSpec
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

    def test_sandboxed_setting_is_honoured(self):
        adapter = OpenCodeAdapter(model="opencode/test", sandboxed=True)
        assert adapter.sandboxed is True
        assert "sandboxed" in adapter.honoured_settings

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

    def test_resolve_model_payload_opencode_provider_is_inferred(self):
        adapter = OpenCodeAdapter(model="opencode/gpt-4")
        payload = adapter._resolve_model_payload({("opencode", "gpt-4")})
        assert payload == {"providerID": "opencode", "modelID": "gpt-4"}

    def test_resolve_model_payload_fallback(self):
        adapter = OpenCodeAdapter(model="claude-sonnet-4-20250514")
        payload = adapter._resolve_model_payload()
        assert payload == {"modelID": "claude-sonnet-4-20250514"}

    def test_model_validation_accepts_server_model(self):
        adapter = OpenCodeAdapter(model="opencode/openai/gpt-5")
        adapter._container = Mock(base_url="http://localhost:55440")
        response = Mock(status_code=200)
        response.json.return_value = {
            "providers": {
                "openai": {"models": {"gpt-5": {}}},
            },
        }

        with patch("httpx.get", return_value=response) as mock_get:
            adapter._validate_model_available()

        mock_get.assert_called_once_with(
            "http://localhost:55440/config/providers", timeout=10.0
        )

    def test_model_validation_rejects_model_without_server_provider(self):
        adapter = OpenCodeAdapter(model="opencode/not-served")
        adapter._container = Mock(base_url="http://localhost:55440")
        response = Mock(status_code=200)
        response.json.return_value = {
            "providers": {"opencode": {"models": {"big-pickle": {}}}},
        }

        with patch("httpx.get", return_value=response):
            with pytest.raises(CoderUnavailableError, match="no server provider"):
                adapter._validate_model_available()

    def test_model_validation_rejects_ambiguous_unqualified_model(self):
        adapter = OpenCodeAdapter(model="opencode/shared")
        adapter._container = Mock(base_url="http://localhost:55440")
        response = Mock(status_code=200)
        response.json.return_value = {
            "providers": {
                "one": {"models": {"shared": {}}},
                "two": {"models": {"shared": {}}},
            },
        }

        with patch("httpx.get", return_value=response):
            with pytest.raises(CoderUnavailableError, match="more than one"):
                adapter._validate_model_available()

    def test_model_validation_infers_provider_used_by_message(self):
        adapter = OpenCodeAdapter(model="opencode/big-pickle")
        adapter._container = Mock(base_url="http://localhost:55440")
        response = Mock(status_code=200)
        response.json.return_value = {
            "providers": {"opencode": {"models": {"big-pickle": {}}}},
        }

        with patch("httpx.get", return_value=response):
            adapter._validate_model_available()

        assert adapter._resolved_model_payload == {
            "providerID": "opencode",
            "modelID": "big-pickle",
        }

    def test_model_validation_names_requested_and_available_models(self):
        adapter = OpenCodeAdapter(model="opencode-cli/openai/gpt-5.6-luna")
        adapter._container = Mock(base_url="http://localhost:55440")
        response = Mock(status_code=200)
        response.json.return_value = {
            "providers": {
                "openai": {"models": {"gpt-5": {}}},
                "anthropic": {"models": {"claude-sonnet": {}}},
            },
        }

        with patch("httpx.get", return_value=response):
            with pytest.raises(CoderUnavailableError) as exc_info:
                adapter._validate_model_available()

        message = str(exc_info.value)
        assert "opencode-cli/openai/gpt-5.6-luna" in message
        assert "openai/gpt-5" in message
        assert "anthropic/claude-sonnet" in message

    def test_model_validation_happens_before_session_creation(self):
        adapter = OpenCodeAdapter(model="opencode-cli/openai/gpt-5.6-luna")
        adapter._container = Mock(base_url="http://localhost:55440")
        adapter._container.is_running.return_value = True
        response = Mock(status_code=200)
        response.json.return_value = {"providers": {"openai": {"models": {"gpt-5": {}}}}}

        with patch("httpx.get", return_value=response):
            with patch.object(adapter, "_create_session") as create_session:
                with pytest.raises(CoderUnavailableError):
                    adapter.implement(TaskSpec(description="test", constraints=[]))

        create_session.assert_not_called()

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

    def test_session_creation_has_no_model(self):
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
            assert body == {}


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

    def test_committed_changes_are_detected_when_no_unstaged(self, git_workspace):
        """When an external coder (agy/opencode) commits its own changes,
        _read_changes_from_disk falls back to reading HEAD_before..HEAD."""
        # Record HEAD before coder runs
        adapter = OpenCodeAdapter(model="opencode/test", workspace=git_workspace)
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(git_workspace), check=True, capture_output=True, text=True,
        )
        adapter._head_before_run = result.stdout.strip()

        # Simulate an external coder committing changes directly
        new_file = git_workspace / "coder_added.py"
        new_file.write_text("# coder added this")
        subprocess.run(
            ["git", "add", "coder_added.py"],
            cwd=str(git_workspace), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "coder: apply changes"],
            cwd=str(git_workspace), check=True, capture_output=True,
        )
        # Now working tree is clean (everything committed)
        entries = adapter._read_changes_from_disk()
        # Should detect the committed change via HEAD_before..HEAD fallback
        assert any(e["file"] == "coder_added.py" for e in entries)

    def test_only_committed_changes_detected_when_working_tree_clean(self, git_workspace):
        """When working tree is clean but HEAD moved (external coder committed),
        the fallback to HEAD_before..HEAD should detect the changes."""
        # Record the initial HEAD
        adapter = OpenCodeAdapter(model="opencode/test", workspace=git_workspace)
        # Simulate recording HEAD before coder run
        subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(git_workspace), check=True, capture_output=True, text=True,
        )
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(git_workspace), check=True, capture_output=True, text=True,
        )
        adapter._head_before_run = result.stdout.strip()

        # Now coder commits changes
        committed_file = git_workspace / "committed.py"
        committed_file.write_text("# committed by coder")
        subprocess.run(
            ["git", "add", "committed.py"],
            cwd=str(git_workspace), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "coder: apply changes"],
            cwd=str(git_workspace), check=True, capture_output=True,
        )
        # Working tree is now clean - all changes are committed
        entries = adapter._read_changes_from_disk()
        paths = [e["file"] for e in entries]
        assert "committed.py" in paths

    def test_no_false_positive_when_coder_commits_nothing(self, git_workspace):
        """When the coder does nothing and HEAD doesn't move,
        _read_changes_from_disk returns empty (not main's last commit)."""
        # First make a commit on main (simulating prior work)
        main_file = git_workspace / "main_work.py"
        main_file.write_text("# main work")
        subprocess.run(
            ["git", "add", "main_work.py"],
            cwd=str(git_workspace), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "commit", "-m", "feat: main work"],
            cwd=str(git_workspace), check=True, capture_output=True,
        )
        # Record HEAD before "coder run"
        adapter = OpenCodeAdapter(model="opencode/test", workspace=git_workspace)
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(git_workspace), check=True, capture_output=True, text=True,
        )
        adapter._head_before_run = result.stdout.strip()

        # Coder does nothing - HEAD stays the same
        entries = adapter._read_changes_from_disk()
        # Should be empty, NOT report main_work.py as coder's work
        assert entries == []


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

    @pytest.fixture(autouse=True)
    def model_validation(self, monkeypatch):
        """Keep readback-flow tests focused on artifact handling."""
        monkeypatch.setattr(
            OpenCodeAdapter, "_validate_model_available", lambda self: None
        )

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

    def test_container_stopped_when_task_halts(self):
        """A failed task does not leave its opencode container running."""
        adapter = OpenCodeAdapter(model="opencode/test", workspace=Path("/tmp"))
        container_mock = Mock()
        container_mock.is_running.return_value = True
        container_mock.base_url = "http://localhost:55440"
        adapter._container = container_mock

        with patch.object(adapter, "_create_session", side_effect=RuntimeError("halt")):
            with pytest.raises(RuntimeError, match="halt"):
                adapter.implement(TaskSpec(description="test", constraints=[]))

        container_mock.stop.assert_called_once()

    def test_workspace_pull_failure_does_not_look_successful(self, git_workspace):
        """A remote readback failure aborts before an artifact can be returned."""
        adapter = OpenCodeAdapter(model="opencode/test", workspace=git_workspace)
        container_mock = Mock()
        container_mock.is_running.return_value = True
        container_mock.base_url = "http://daemon.example:55440"
        container_mock.sync_workspace_from_container.side_effect = OSError("connection lost")
        adapter._container = container_mock

        with patch.object(adapter, "_create_session", return_value="session-1"):
            with patch.object(adapter, "_wait_for_completion"):
                with pytest.raises(LLMCallError, match="retrieve opencode workspace"):
                    adapter.implement(TaskSpec(description="test", constraints=[]))

        container_mock.stop.assert_called_once()

    def test_contained_workspace_pull_failure_is_environment_error(self, git_workspace):
        """A declared run refuses when its copied workspace cannot return."""
        adapter = OpenCodeAdapter(
            model="opencode/test", workspace=git_workspace, sandboxed=True
        )
        container_mock = Mock()
        container_mock.is_running.return_value = True
        container_mock.base_url = "http://daemon.example:55440"
        container_mock.sync_workspace_from_container.side_effect = OSError("copy unavailable")
        adapter._container = container_mock

        with patch.object(adapter, "_create_session", return_value="session-1"):
            with patch.object(adapter, "_wait_for_completion"):
                with pytest.raises(CoderUnavailableError, match="read back"):
                    adapter.implement(TaskSpec(description="test", constraints=[]))

        container_mock.start.assert_called_once_with(
            git_workspace, task_id="", contained=True
        )
