"""Tests for .snodo/ protection from tool-surface mutation (ADR 026, Fixes #34).

Covers:
- Tool-surface write to .snodo/protocol.yml is refused (PathValidationError).
- Tool-surface delete and create_directory under .snodo/ are refused.
- Reading .snodo/protocol.yml still succeeds.
- GitMCP stage_files for .snodo/ is refused.
- Coder adapters (LiteLLM, Mock, OpenCode, OpenCodeCLI) do not report .snodo/ as an artifact.
- Internal state writes (audit log, session state, project state) succeed unimpeded.
"""

import tempfile
import subprocess
from pathlib import Path

import pytest

from snodo.tools.workspace import WorkspaceMCP, PathValidationError
from snodo.tools.git import GitMCP
from snodo.core.interfaces import TaskSpec, FileArtifact
from snodo.coders.litellm import LiteLLMAdapter
from snodo.coders.mock import MockAdapter
from snodo.coders.opencode_adapter import OpenCodeAdapter
from snodo.coders.opencode_cli_adapter import OpenCodeCLIAdapter
from snodo.infrastructure.audit import AuditLog
from snodo.infrastructure.session import SessionManager
from snodo.infrastructure.state import write_state, read_state, ProjectState


@pytest.fixture
def workspace_with_snodo():
    """Create a temporary project root with a .snodo/ directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        snodo_dir = root / ".snodo"
        snodo_dir.mkdir()
        proto_file = snodo_dir / "protocol.yml"
        proto_file.write_text("name: Test Protocol\n")

        ws = WorkspaceMCP(str(root))
        yield root, ws


# === WorkspaceMCP protection tests ===

def test_workspace_write_to_snodo_refused(workspace_with_snodo):
    """Writing to .snodo/protocol.yml via WorkspaceMCP is refused."""
    root, ws = workspace_with_snodo
    with pytest.raises(PathValidationError) as excinfo:
        ws.write_file(".snodo/protocol.yml", "mutated: true")

    err = str(excinfo.value)
    assert "protected under .snodo/" in err
    assert ".snodo/protocol.yml" in err
    # Confirm file was not mutated
    assert (root / ".snodo" / "protocol.yml").read_text() == "name: Test Protocol\n"


def test_workspace_delete_under_snodo_refused(workspace_with_snodo):
    """Deleting a file under .snodo/ via WorkspaceMCP is refused."""
    root, ws = workspace_with_snodo
    with pytest.raises(PathValidationError) as excinfo:
        ws.delete_file(".snodo/protocol.yml")

    err = str(excinfo.value)
    assert "protected under .snodo/" in err
    assert (root / ".snodo" / "protocol.yml").exists()


def test_workspace_mkdir_under_snodo_refused(workspace_with_snodo):
    """Creating a directory under .snodo/ via WorkspaceMCP is refused."""
    root, ws = workspace_with_snodo
    with pytest.raises(PathValidationError) as excinfo:
        ws.create_directory(".snodo/new_dir")

    err = str(excinfo.value)
    assert "protected under .snodo/" in err
    assert not (root / ".snodo" / "new_dir").exists()


def test_workspace_read_under_snodo_succeeds(workspace_with_snodo):
    """Reading .snodo/protocol.yml via WorkspaceMCP succeeds."""
    root, ws = workspace_with_snodo
    content = ws.read_file(".snodo/protocol.yml")
    assert content == "name: Test Protocol\n"

    lines = ws.read_file_lines(".snodo/protocol.yml", 1, 1)
    assert lines == "name: Test Protocol"

    assert ws.file_exists(".snodo/protocol.yml")
    assert "protocol.yml" in ws.list_files(".snodo")


# === GitMCP protection tests ===

def test_git_stage_files_under_snodo_refused():
    """Staging .snodo/ files via GitMCP is refused."""
    with tempfile.TemporaryDirectory() as tmpdir:
        subprocess.run(["git", "init", "-q"], cwd=tmpdir, check=True)
        snodo_dir = Path(tmpdir) / ".snodo"
        snodo_dir.mkdir()
        (snodo_dir / "protocol.yml").write_text("name: Test\n")

        git_mcp = GitMCP(tmpdir)
        with pytest.raises(PathValidationError) as excinfo:
            git_mcp.stage_files([".snodo/protocol.yml"])

        err = str(excinfo.value)
        assert "protected under .snodo/" in err


# === Coder Adapter artifact filtering tests ===

def test_litellm_adapter_excludes_snodo_artifacts():
    """LiteLLMAdapter ignores file operations under .snodo/."""
    adapter = LiteLLMAdapter()
    json_response = '''[
        {"path": "src/app.py", "content": "print('hello')", "action": "write"},
        {"path": ".snodo/protocol.yml", "content": "malicious edit", "action": "write"}
    ]'''
    artifact = adapter._parse_response(json_response)
    paths = [f.path for f in artifact.files]
    assert "src/app.py" in paths
    assert ".snodo/protocol.yml" not in paths


def test_mock_adapter_excludes_snodo_artifacts():
    """MockAdapter filters out .snodo/ artifacts."""
    adapter = MockAdapter(mock_files=[
        FileArtifact(path="src/main.py", content="code"),
        FileArtifact(path=".snodo/protocol.yml", content="hacked"),
    ])
    spec = TaskSpec(description="test task", constraints=[])
    artifact = adapter.implement(spec)
    paths = [f.path for f in artifact.files]
    assert "src/main.py" in paths
    assert ".snodo/protocol.yml" not in paths


def test_opencode_adapter_diff_to_artifact_excludes_snodo():
    """OpenCodeAdapter._diff_to_artifact excludes files under .snodo/."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        snodo_dir = root / ".snodo"
        snodo_dir.mkdir()
        (snodo_dir / "protocol.yml").write_text("hacked")
        (root / "app.py").write_text("print('ok')")

        adapter = OpenCodeAdapter(workspace=str(root))
        diff_entries = [
            {"file": "app.py", "status": "modified"},
            {"file": ".snodo/protocol.yml", "status": "modified"},
        ]
        artifact = adapter._diff_to_artifact(diff_entries)
        paths = [f.path for f in artifact.files]
        assert "app.py" in paths
        assert ".snodo/protocol.yml" not in paths


def test_opencode_cli_adapter_diff_to_artifact_excludes_snodo():
    """OpenCodeCLIAdapter._diff_to_artifact excludes files under .snodo/."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        snodo_dir = root / ".snodo"
        snodo_dir.mkdir()
        (snodo_dir / "protocol.yml").write_text("hacked")
        (root / "main.py").write_text("main()")

        adapter = OpenCodeCLIAdapter(workspace=str(root))
        diff_entries = [
            {"file": "main.py", "status": "modified"},
            {"file": ".snodo/protocol.yml", "status": "modified"},
        ]
        artifact = adapter._diff_to_artifact(diff_entries)
        paths = [f.path for f in artifact.files]
        assert "main.py" in paths
        assert ".snodo/protocol.yml" not in paths


# === Internal Snodo state write tests ===

def test_internal_snodo_state_writes_unimpeded():
    """Internal snodo state operations write directly without WorkspaceMCP path blocking."""
    with tempfile.TemporaryDirectory() as tmpdir:
        root = Path(tmpdir)
        snodo_dir = root / ".snodo"
        snodo_dir.mkdir()

        # AuditLog write
        audit = AuditLog(log_path=str(snodo_dir / "audit.log"))
        audit.append_event("test_event", {"key": "val"})
        assert (snodo_dir / "audit.log").exists()

        # ProjectState write
        write_state(str(root), ProjectState(current_mode="producer", active_session={}))
        assert (snodo_dir / "state.json").exists()
        loaded = read_state(str(root))
        assert loaded.current_mode == "producer"

        # SessionManager write
        sm = SessionManager(sessions_dir=snodo_dir / "sessions")
        session = sm.create_session("producer", str(root))
        assert session is not None
        assert (snodo_dir / "sessions").exists()
