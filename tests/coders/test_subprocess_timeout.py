"""Tests for subprocess coder timeout handling, output capture, and artifact recovery.

FILE: tests/coders/test_subprocess_timeout.py
"""

import signal
import subprocess
from pathlib import Path
from unittest import mock
import pytest

from snodo.coders.agy_adapter import AGYAdapter
from snodo.coders.base import LLMCallError
from snodo.coders.opencode_cli_adapter import OpenCodeCLIAdapter
from snodo.compiler.models import Mode, Protocol, Validator
from snodo.core.interfaces import Task, TaskSpec
from snodo.engine.loop import GraphBuilder, build_protocol_graph
from snodo.infrastructure.config import CoderConfig, LlmConfig
from snodo.tools.git import GitMCP
from snodo.tools.workspace import WorkspaceMCP


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    (root / "README.md").write_text("# Test Workspace\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
    return root


class TestSubprocessTimeout:
    def test_timed_out_run_with_commits_reports_commits_rather_than_zero_artifacts(
        self, temp_workspace: Path
    ):
        """A timed-out run whose coder committed reports those commits rather than 0 artifacts."""
        adapter = AGYAdapter(workspace=temp_workspace)
        spec = TaskSpec(description="Build feature", constraints=[])

        def fake_run_timeout_after_commits(*args, **kwargs):
            # Coder writes files and makes git commits before timing out
            (temp_workspace / "file_a.py").write_text("def a(): return 1\n")
            subprocess.run(["git", "add", "file_a.py"], cwd=temp_workspace, check=True)
            subprocess.run(["git", "commit", "-m", "commit a"], cwd=temp_workspace, check=True)

            (temp_workspace / "file_b.py").write_text("def b(): return 2\n")
            subprocess.run(["git", "add", "file_b.py"], cwd=temp_workspace, check=True)
            subprocess.run(["git", "commit", "-m", "commit b"], cwd=temp_workspace, check=True)

            raise subprocess.TimeoutExpired(
                cmd=["agy"],
                timeout=1800,
                output="Created file_a.py, committed. Created file_b.py, committed.",
                stderr="",
            )

        with mock.patch.object(adapter, "_run_subprocess", side_effect=fake_run_timeout_after_commits):
            artifact = adapter.implement(spec)

        # Artifacts should not be empty — the 2 committed files must survive and be returned
        assert len(artifact.files) >= 2
        file_paths = [f.path for f in artifact.files]
        assert "file_a.py" in file_paths
        assert "file_b.py" in file_paths

    def test_timed_out_run_with_commits_in_engine_loop(self, temp_workspace: Path):
        """Through the engine loop, a timed-out run that committed preserves artifacts."""
        protocol = Protocol(
            protocol_id="test-proto",
            name="test-proto",
            initial_mode="producer",
            modes=[
                Mode(mode_id="producer", name="producer", coder="agy"),
            ],
            validators=[
                Validator(
                    validator_id="v1",
                    validator_type="test",
                    description="test validator",
                    prompt="test",
                )
            ],
        )
        task = Task(id="t1", spec="build feature")

        adapter = AGYAdapter(workspace=temp_workspace)

        def fake_run_timeout_after_commits(*args, **kwargs):
            (temp_workspace / "module.py").write_text("def x(): pass\n")
            subprocess.run(["git", "add", "module.py"], cwd=temp_workspace, check=True)
            subprocess.run(["git", "commit", "-m", "feature commit"], cwd=temp_workspace, check=True)

            raise subprocess.TimeoutExpired(
                cmd=["agy"],
                timeout=1800,
                output="Created module.py, committed.",
                stderr="",
            )

        with mock.patch.object(adapter, "_run_subprocess", side_effect=fake_run_timeout_after_commits):
            builder = GraphBuilder(
                protocol=protocol,
                workspace_mcp=WorkspaceMCP(str(temp_workspace)),
                git_mcp=GitMCP(str(temp_workspace)),
                coder=adapter,
                project_root=str(temp_workspace),
            )

            state = {
                "task": task.model_dump(),
                "current_mode": "producer",
                "validation_token": {"jwt": "valid_token"},
                "is_blocked": False,
                "artifacts": [],
                "metadata": {},
                "summary": "",
            }
            with mock.patch.object(builder._token_issuer, "verify_token", return_value=True):
                with mock.patch.object(builder._token_issuer, "consume_token"):
                    result = builder._execute_node(state)

        # Artifacts must not be zero
        assert len(result["artifacts"]) > 0
        assert "module.py" in result["artifacts"]
        assert result.get("is_blocked") is not True

    def test_captured_output_survives_into_failure(self, temp_workspace: Path):
        """When a timed-out run produces no commits, captured stderr/stdout survives into LLMCallError."""
        adapter = OpenCodeCLIAdapter(workspace=temp_workspace)
        spec = TaskSpec(description="Slow task", constraints=[])

        def fake_run_timeout_no_commits(*args, **kwargs):
            raise subprocess.TimeoutExpired(
                cmd=["opencode"],
                timeout=1800,
                output="Step 1: Reading repo... Step 2: Still analyzing AST...",
                stderr="Warning: slow memory allocation in progress",
            )

        with mock.patch.object(adapter, "_run_subprocess", side_effect=fake_run_timeout_no_commits):
            with pytest.raises(LLMCallError) as exc_info:
                adapter.implement(spec)

        msg = str(exc_info.value)
        assert "opencode run timed out after 1800s" in msg
        # The captured output / stderr must survive into the exception message
        assert "Warning: slow memory allocation" in msg or "Step 1: Reading repo" in msg

    def test_operator_set_timeout_is_honoured_in_adapter(self, temp_workspace: Path):
        """An operator-configured timeout is stored and used by the adapter."""
        adapter = AGYAdapter(workspace=temp_workspace, timeout_seconds=120)
        assert adapter.timeout_seconds == 120

        called_timeout = None

        def fake_run(argv, cwd, timeout):
            nonlocal called_timeout
            called_timeout = timeout
            return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout="", stderr="")
            adapter._run_subprocess(["agy", "-p", "test"], str(temp_workspace))
            assert mock_run.call_args.kwargs["timeout"] == 120

    def test_operator_set_timeout_honoured_via_config_and_mode(self, temp_workspace: Path):
        """Timeout configured in LlmConfig and overridden in Mode.coder_config is honoured."""
        protocol = Protocol(
            protocol_id="test-proto",
            name="test-proto",
            initial_mode="producer",
            modes=[
                Mode(
                    mode_id="producer",
                    name="producer",
                    coder="agy",
                    coder_config={"timeout_seconds": 250},
                ),
            ],
            validators=[
                Validator(
                    validator_id="v1",
                    validator_type="test",
                    description="test validator",
                    prompt="test",
                )
            ],
        )

        custom_cfg = LlmConfig(coder=CoderConfig(timeout_seconds=500))
        with mock.patch("snodo.infrastructure.config.load_llm_config", return_value=custom_cfg):
            with mock.patch("snodo.engine.loop.GraphBuilder") as mock_gb:
                build_protocol_graph(
                    protocol=protocol,
                    workspace_mcp=WorkspaceMCP(str(temp_workspace)),
                    git_mcp=GitMCP(str(temp_workspace)),
                    project_root=str(temp_workspace),
                )
                passed_coder = mock_gb.call_args.kwargs["coder"]
                assert passed_coder.timeout_seconds == 250

    def test_subprocess_timeout_kills_process_group_when_spawned(self, temp_workspace: Path):
        """_run_subprocess uses start_new_session and kills the process group on timeout."""
        adapter = AGYAdapter(workspace=temp_workspace, timeout_seconds=1)

        mock_proc = mock.MagicMock()
        mock_proc.pid = 99999
        mock_proc.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd=["agy"], timeout=1),
            ("partial stdout", "partial stderr"),
        ]

        with mock.patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
            with mock.patch("os.killpg") as mock_killpg:
                with mock.patch("os.getpgid", return_value=99999):
                    with pytest.raises(subprocess.TimeoutExpired) as exc_info:
                        adapter._run_subprocess(["agy"], str(temp_workspace))

                    mock_popen.assert_called_once()
                    assert mock_popen.call_args.kwargs.get("start_new_session") is True
                    mock_killpg.assert_called_once_with(99999, signal.SIGKILL)
                    assert exc_info.value.output == "partial stdout"
                    assert exc_info.value.stderr == "partial stderr"
