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

import io
import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import git  # noqa: F401 - initialize before tests mock subprocess.Popen.
import pytest
from snodo.coders.opencode_cli_adapter import OpenCodeCLIAdapter
from snodo.core.interfaces import TaskSpec
from snodo.tools.workspace import WorkspaceMCP


def test_opencode_cli_prompt_includes_engine_boundary_guidance(tmp_path):
    adapter = OpenCodeCLIAdapter(workspace=tmp_path)
    adapter.protected_paths = ["docs/policy.md"]
    prompt = adapter._build_prompt(TaskSpec(description="do work", constraints=[]))
    assert "docs/policy.md" in prompt
    assert ".snodo/" in prompt
    assert "not a sandbox" in prompt
    assert "Workspace Confinement" in prompt
    assert "dispatched workspace is the only place" in prompt

_REAL_CLI_MAJOR_VERSION = OpenCodeCLIAdapter._cli_major_version


@pytest.fixture(autouse=True)
def _opencode_v1_by_default():
    """Pin the host CLI generation so argv tests never depend on the installed opencode."""
    with patch.object(OpenCodeCLIAdapter, "_cli_major_version", return_value=1):
        yield


# ========== WORKSPACE PARAM RESOLUTION ==========


class TestWorkspaceResolution:
    """OpenCodeCLIAdapter accepts workspace from multiple sources and refuses missing/invalid."""

    def test_refuses_when_no_workspace_or_workspace_mcp(self):
        """Constructing without workspace or workspace_mcp refuses loudly with ValueError."""
        with pytest.raises(ValueError, match="requires an explicit workspace or workspace_mcp"):
            OpenCodeCLIAdapter(model="opencode-cli/test")

    def test_refuses_when_invalid_workspace_mcp_object(self):
        """Constructing with workspace_mcp lacking project_root refuses loudly with ValueError."""
        with pytest.raises(ValueError, match="requires an explicit workspace or a valid WorkspaceMCP"):
            OpenCodeCLIAdapter(model="opencode-cli/test", workspace_mcp=object())

    def test_workspace_param_takes_priority(self):
        adapter = OpenCodeCLIAdapter(
            model="opencode-cli/test",
            workspace=Path("/custom/workspace"),
        )
        assert adapter._workspace == Path("/custom/workspace")
        assert adapter.workspace == Path("/custom/workspace")

    def test_workspace_mcp_with_project_root(self):
        workspace_mcp = Mock(spec=WorkspaceMCP)
        workspace_mcp.project_root = Path("/mcp/root")
        adapter = OpenCodeCLIAdapter(
            model="opencode-cli/test",
            workspace_mcp=workspace_mcp,
        )
        assert adapter._workspace == Path("/mcp/root")
        assert adapter.workspace == Path("/mcp/root")

    def test_workspace_overrides_workspace_mcp(self):
        workspace_mcp = Mock(spec=WorkspaceMCP)
        workspace_mcp.project_root = Path("/mcp/root")
        adapter = OpenCodeCLIAdapter(
            model="opencode-cli/test",
            workspace=Path("/explicit"),
            workspace_mcp=workspace_mcp,
        )
        assert adapter._workspace == Path("/explicit")
        assert adapter.workspace == Path("/explicit")

    @pytest.mark.parametrize("major", [1, 2])
    def test_task_worktree_granted_to_cli_distinct_from_cwd(self, tmp_path, major):
        """When task worktree differs from Path.cwd(), the granted dir and cwd for CLI is the worktree."""
        worktree = tmp_path / "task_worktree"
        worktree.mkdir()
        adapter = OpenCodeCLIAdapter(model="opencode-cli/test", workspace=worktree)
        assert adapter.workspace == worktree

        executed_kwargs = {}
        executed_argv = []

        def fake_popen(argv, **kwargs):
            nonlocal executed_argv, executed_kwargs
            executed_argv = argv
            executed_kwargs = kwargs
            (worktree / "output.txt").write_text("done")
            proc = Mock()
            proc.pid = 12345
            proc.returncode = 0
            proc.stdout = io.StringIO("Success")
            proc.stderr = io.StringIO("")
            return proc

        spec = TaskSpec(description="test task", constraints=[])
        with patch("subprocess.Popen", side_effect=fake_popen), \
                patch.object(adapter, "_cli_major_version", return_value=major):
            adapter.implement(spec)

        assert executed_kwargs["cwd"] == str(worktree)
        inline = json.loads(executed_kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
        assert inline["permission"] == {
            "edit": {"*": "deny", str(worktree.resolve() / "**"): "allow"},
            "external_directory": "deny",
        }
        assert inline["tools"] == {"session_move": False, "worktree_create": False}
        assert executed_kwargs["cwd"] != str(Path.cwd())
        if major == 1:
            dir_idx = executed_argv.index("--dir")
            assert executed_argv[dir_idx + 1] == str(worktree)
        else:
            assert "--standalone" in executed_argv
            assert "--attach" not in executed_argv
            assert "--dir" not in executed_argv


# ========== BARE MODEL ==========


class TestBareModel:
    """_bare_model strips this adapter's prefix, and drops anything else."""

    def test_strips_prefix(self):
        adapter = OpenCodeCLIAdapter(model="opencode-cli/deepseek/deepseek-chat", workspace=Path("/dummy"))
        assert adapter._bare_model() == "deepseek/deepseek-chat"

    def test_model_outside_this_namespace_is_dropped(self):
        """A model that is not opencode-cli's is NOT passed through.

        `snodo run -m X` names the model that JUDGES the work — it is resolved
        through litellm for the validators and the classifier. opencode owns
        its own catalog and cannot route it. Forwarding it made the CLI reject
        the run before writing anything, so an unnamespaced model yields "" and
        the CLI falls back to its own default. This test previously asserted
        the passthrough that caused that.
        """
        adapter = OpenCodeCLIAdapter(model="deepseek/deepseek-chat", workspace=Path("/dummy"))
        assert adapter._bare_model() == ""


class TestJsonOutputUsage:
    def test_opencode_argv_requests_json(self, tmp_path):
        adapter = OpenCodeCLIAdapter(model="", workspace=tmp_path)
        assert adapter._build_argv("prompt", str(tmp_path), "") == [
            "opencode", "run", "--format", "json", "--dir", str(tmp_path),
            "--dangerously-skip-permissions", "prompt",
        ]

    @pytest.mark.parametrize(
        "lines, expected",
        [
            ([{"type": "step_finish", "part": {"cost": 0.25, "tokens": {"input": 10, "output": 4, "reasoning": 2, "cache": {"read": 3, "write": 1}}}}],
             {"input_tokens": 10, "output_tokens": 4, "reasoning_tokens": 2, "cache_read_tokens": 3, "cache_write_tokens": 1, "cost": 0.25}),
            ([{"type": "step_finish", "part": {"cost": 0.25, "tokens": {"input": 10, "output": 4, "cache": {"read": 3, "write": 1}}}},
              {"type": "step_finish", "part": {"cost": 0.5, "tokens": {"input": 2, "output": 8, "cache": {"read": 5, "write": 2}}}}],
             {"input_tokens": 12, "output_tokens": 12, "reasoning_tokens": None, "cache_read_tokens": 8, "cache_write_tokens": 3, "cost": 0.75}),
            ([{"type": "text", "part": {"text": "hello"}}],
             {"input_tokens": None, "output_tokens": None, "reasoning_tokens": None, "cache_read_tokens": None, "cache_write_tokens": None, "cost": None}),
        ],
    )
    def test_accumulates_step_finish_usage(self, tmp_path, lines, expected):
        adapter = OpenCodeCLIAdapter(model="", workspace=tmp_path)
        adapter._begin_subprocess_run()
        rendered = [adapter._process_output_line(json.dumps(line)) for line in lines]
        assert adapter.last_usage == expected
        if lines[0]["type"] == "text":
            assert rendered == ["hello"]
        else:
            assert rendered == [""] * len(lines)

    def test_malformed_line_passes_through(self, tmp_path):
        adapter = OpenCodeCLIAdapter(model="", workspace=tmp_path)
        assert adapter._process_output_line("not-json\n") == "not-json"


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

        mock_proc = Mock()
        mock_proc.pid = 12345
        mock_proc.returncode = 0
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("")

        with patch.object(adapter, "_build_prompt", return_value="test prompt"):
            with patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
                adapter.implement(spec)

                call_args = mock_popen.call_args[0][0]
                assert call_args[0] == "opencode"
                assert call_args[1] == "run"
                assert call_args[2:4] == ["--format", "json"]
                assert call_args[4] == "--dir"
                assert call_args[5] == str(git_workspace)
                assert call_args[6] == "--dangerously-skip-permissions"
                assert call_args[7].startswith("test prompt")
                assert call_args[8] == "-m"
                assert call_args[9] == "deepseek/deepseek-chat"

    def test_opencode_not_found_raises(self, git_workspace):
        adapter = OpenCodeCLIAdapter(
            model="opencode-cli/deepseek/deepseek-chat",
            workspace=git_workspace,
        )
        spec = TaskSpec(description="test", constraints=[])

        with patch("subprocess.Popen", side_effect=FileNotFoundError):
            with pytest.raises(Exception, match="opencode not found"):
                adapter.implement(spec)

    def test_non_zero_exit_raises(self, git_workspace):
        adapter = OpenCodeCLIAdapter(
            model="opencode-cli/test",
            workspace=git_workspace,
        )
        spec = TaskSpec(description="test", constraints=[])

        mock_proc = Mock()
        mock_proc.pid = 12345
        mock_proc.returncode = 1
        mock_proc.stdout = io.StringIO("")
        mock_proc.stderr = io.StringIO("something went wrong")

        with patch("subprocess.Popen", return_value=mock_proc):
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

        coder = get_coder("opencode-cli", model="opencode-cli/deepseek/deepseek-chat", workspace=Path("/tmp"))
        assert isinstance(coder, OpenCodeCLIAdapter)
        assert coder.model == "opencode-cli/deepseek/deepseek-chat"
        assert coder.workspace == Path("/tmp")


# ========== OPENCODE 2.x ==========


class TestOpenCodeV2:
    """opencode 2.x removed --dir and --dangerously-skip-permissions."""

    def test_v2_argv_uses_standalone(self, tmp_path):
        adapter = OpenCodeCLIAdapter(model="", workspace=tmp_path)
        with patch.object(OpenCodeCLIAdapter, "_cli_major_version", return_value=2):
            argv = adapter._build_argv("prompt", str(tmp_path), "openai/gpt-6-luna")
        assert argv == [
            "opencode", "run", "--format", "json", "--standalone",
            "prompt", "-m", "openai/gpt-6-luna",
        ]
        assert "--dir" not in argv
        assert "--dangerously-skip-permissions" not in argv


class TestSnodoCloudProvider:
    def test_cloud_model_gets_per_run_provider_and_credential_stays_out_of_argv(self, tmp_path):
        adapter = OpenCodeCLIAdapter(
            model="opencode-cli/snodo-cloud/gpt-6-luna", workspace=tmp_path,
        )
        manager = Mock()
        manager.get_key_for_model.return_value = "secret-token"
        manager.load.return_value = {"cloud": {"models_url": "https://gateway.example/v1"}}
        with patch("snodo.coders.opencode_cli_adapter.ConfigManager", return_value=manager):
            env = adapter._subprocess_env()

        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        provider = config["provider"]["snodo-cloud"]
        assert provider["options"]["baseURL"] == "https://gateway.example/v1"
        assert provider["models"] == {"gpt-6-luna": {"name": "gpt-6-luna"}}
        assert config["permission"]["edit"] == {"*": "deny", str(tmp_path.resolve() / "**"): "allow"}
        assert config["permission"]["external_directory"] == "deny"
        assert config["tools"] == {"session_move": False, "worktree_create": False}
        argv = adapter._build_argv("prompt", str(tmp_path), adapter._bare_model())
        assert argv[-2:] == ["-m", "snodo-cloud/gpt-6-luna"]
        assert "secret-token" not in argv

    def test_other_models_do_not_get_provider_injection(self, tmp_path):
        adapter = OpenCodeCLIAdapter(model="opencode-cli/openai/gpt-6-luna", workspace=tmp_path)
        config = json.loads(adapter._subprocess_env()["OPENCODE_CONFIG_CONTENT"])
        assert config["permission"]["edit"] == {"*": "deny", str(tmp_path.resolve() / "**"): "allow"}
        assert config["permission"]["external_directory"] == "deny"
        assert config["tools"] == {"session_move": False, "worktree_create": False}

    def test_missing_cloud_credential_fails_with_login_guidance(self, tmp_path):
        adapter = OpenCodeCLIAdapter(model="opencode-cli/snodo-cloud/gpt-6-luna", workspace=tmp_path)
        manager = Mock()
        manager.get_key_for_model.return_value = None
        with patch("snodo.coders.opencode_cli_adapter.ConfigManager", return_value=manager):
            with pytest.raises(ValueError, match="snodo cloud login"):
                adapter._subprocess_env()

    @pytest.mark.parametrize(
        "reported, expected",
        [("opencode v2.0.28", 2), ("1.14.3", 1), ("", 1), ("unknown", 1)],
    )
    def test_major_version_parsed_from_version_output(self, tmp_path, reported, expected):
        adapter = OpenCodeCLIAdapter(model="", workspace=tmp_path)
        with patch.object(OpenCodeCLIAdapter, "_cli_major_version", _REAL_CLI_MAJOR_VERSION), \
                patch("snodo.coders.availability.read_binary_version", return_value=reported), \
                patch.object(adapter, "_resolve_binary_path", return_value="/usr/bin/opencode"):
            assert adapter._cli_major_version() == expected

    def test_v2_events_are_rendered_and_usage_counted(self, tmp_path):
        adapter = OpenCodeCLIAdapter(model="", workspace=tmp_path)
        adapter._begin_subprocess_run()
        tool = {"type": "tool_use", "part": {"tool": "patch", "state": {"status": "completed", "title": "patch"}}}
        finish = {"type": "step_finish", "part": {"cost": 0, "tokens": {"input": 5422, "output": 34, "reasoning": 0, "cache": {"read": 1536, "write": 0}}}}
        text = {"type": "text", "part": {"text": "Created hello.txt"}}
        error = {"type": "error", "error": {"type": "provider.invalid-request", "message": "No endpoints found that support tool use."}}
        assert adapter._process_output_line(json.dumps(tool)) == "[patch] patch"
        assert adapter._process_output_line(json.dumps(finish)) == ""
        assert adapter._process_output_line(json.dumps(text)) == "Created hello.txt"
        assert adapter._process_output_line(json.dumps(error)) == "[error] No endpoints found that support tool use."
        assert adapter.last_usage["input_tokens"] == 5422
        assert adapter.last_usage["cache_read_tokens"] == 1536
        tail = adapter._format_output_tail_stdout("\n".join(json.dumps(e) for e in (tool, text, error)))
        assert tail.splitlines() == [
            "[patch] patch", "Created hello.txt", "[error] No endpoints found that support tool use.",
        ]


# ========== OPENCODE CONFIG CONFINEMENT MERGE ==========


class TestOpenCodeConfigConfinement:
    """_subprocess_env merges confinement into OPENCODE_CONFIG_CONTENT safely."""

    @pytest.mark.parametrize("inherited", [
        "not-json",
        "[]",
        "null",
    ])
    def test_malformed_or_non_object_config_still_gets_confinement(self, tmp_path, inherited):
        """Non-mergeable OPENCODE_CONFIG_CONTENT values are replaced with confined object.

        The adapter must still confine edits and disable relocation tools even when the
        inherited value cannot be merged into (invalid JSON or non-object).
        """
        adapter = OpenCodeCLIAdapter(model="opencode-cli/test", workspace=tmp_path)

        with patch("snodo.coders.opencode_cli_adapter.subprocess_env_for_coder") as env_factory:
            env_factory.return_value = {"OPENCODE_CONFIG_CONTENT": inherited}
            env = adapter._subprocess_env()

        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert isinstance(config, dict)
        assert config["permission"]["edit"] == {"*": "deny", str(tmp_path.resolve() / "**"): "allow"}
        assert config["permission"]["external_directory"] == "deny"
        assert config["tools"] == {"session_move": False, "worktree_create": False}

    def test_object_config_keeps_unrelated_keys_when_confinement_added(self, tmp_path):
        """Existing sibling keys survive when confinement is merged into object config."""
        adapter = OpenCodeCLIAdapter(model="opencode-cli/test", workspace=tmp_path)
        existing = {
            "unrelated": {"keep": True},
            "permission": {"other": "value"},
            "tools": {"other_tool": True},
        }

        with patch("snodo.coders.opencode_cli_adapter.subprocess_env_for_coder") as env_factory:
            env_factory.return_value = {
                "OPENCODE_CONFIG_CONTENT": json.dumps(existing),
            }
            env = adapter._subprocess_env()

        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        assert config["unrelated"] == {"keep": True}
        # Existing permission/tools keys are merged with confinement rules.
        assert config["permission"]["edit"] == {"*": "deny", str(tmp_path.resolve() / "**"): "allow"}
        assert config["permission"]["external_directory"] == "deny"
        assert config["permission"]["other"] == "value"
        assert config["tools"]["session_move"] is False
        assert config["tools"]["worktree_create"] is False
        assert config["tools"]["other_tool"] is True

    def test_cloud_provider_merge_survives_non_dict_provider_key(self, tmp_path):
        """Cloud provider injection overwrites non-dict provider while keeping confinement."""
        adapter = OpenCodeCLIAdapter(
            model="opencode-cli/snodo-cloud/gpt-6-luna", workspace=tmp_path,
        )

        # Start with a non-dict provider plus some unrelated configuration.
        base_config = {
            "provider": "unexpected-string",
            "unrelated": {"keep": True},
        }

        manager = Mock()
        manager.get_key_for_model.return_value = "secret-token"
        manager.load.return_value = {"cloud": {"models_url": "https://gateway.example/v1"}}

        with patch("snodo.coders.opencode_cli_adapter.subprocess_env_for_coder") as env_factory, \
                patch("snodo.coders.opencode_cli_adapter.ConfigManager", return_value=manager):
            env_factory.return_value = {
                "OPENCODE_CONFIG_CONTENT": json.dumps(base_config),
            }
            env = adapter._subprocess_env()

        config = json.loads(env["OPENCODE_CONFIG_CONTENT"])
        # Confinement keys remain present.
        assert config["permission"]["edit"] == {"*": "deny", str(tmp_path.resolve() / "**"): "allow"}
        assert config["permission"]["external_directory"] == "deny"
        assert config["tools"] == {"session_move": False, "worktree_create": False}
        # Unrelated keys survive.
        assert config["unrelated"] == {"keep": True}
        # Provider block is now a dict with snodo-cloud entry, replacing the non-dict.
        provider = config["provider"]["snodo-cloud"]
        assert provider["options"]["baseURL"] == "https://gateway.example/v1"
        assert provider["options"]["apiKey"] == "secret-token"
        assert provider["models"] == {"gpt-6-luna": {"name": "gpt-6-luna"}}
