"""Tests for .git protection from tool-surface access (Fixes #80).

Covers:
- WorkspaceMCP validates `.git` and paths under `.git` as protected paths.
- `.git` reads, line reads, listings, existence checks, and mutations are refused.
- `.gitignore` and normal project files remain accessible.
- ProtocolMCPServer read/list tools surface the refusal through MCPError.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from snodo.compiler.models import Protocol
from snodo.mcp.server import MCPError, ProtocolMCPServer
from snodo.tools.workspace import PathValidationError, WorkspaceMCP


@pytest.fixture
def workspace_with_dotgit(tmp_path):
    """Create a temporary project root containing representative .git files."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[user]\n\tname = secret\n")
    (git_dir / "HEAD").write_text("ref: refs/heads/main")
    (tmp_path / ".gitignore").write_text("dist/\n")
    (tmp_path / "README.md").write_text("readme\n")

    yield tmp_path, WorkspaceMCP(str(tmp_path))


@pytest.fixture
def protocol():
    return Protocol(
        protocol_id="test",
        name="Test Protocol",
        version="1.0.0",
        modes=[
            {
                "mode_id": "producer",
                "name": "Producer",
                "tools": ["edit", "test"],
                "validators": ["security"],
            },
            {
                "mode_id": "reviewer",
                "name": "Reviewer",
                "tools": ["review", "approve"],
                "validators": ["security"],
            },
        ],
        validators=[
            {
                "validator_id": "security",
                "validator_type": "security",
                "criteria": ["Check security"],
            },
        ],
        disagreement_policy="unanimous",
        initial_mode="producer",
    )


@pytest.fixture
def project_dir():
    """Create a temporary git-backed project root for MCP server tests."""
    project = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=project, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project, check=True)
    Path(project, "README.md").write_text("readme")
    subprocess.run(["git", "add", "."], cwd=project, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=project, check=True)
    yield project
    shutil.rmtree(project, ignore_errors=True)


@pytest.fixture
def server(protocol, project_dir):
    return ProtocolMCPServer(protocol, project_dir)


def test_validate_path_blocks_dotgit(workspace_with_dotgit):
    _, ws = workspace_with_dotgit

    with pytest.raises(PathValidationError, match=r"protected under \.git/"):
        ws.validate_path(".git/config")


def test_validate_path_blocks_exact_dotgit(workspace_with_dotgit):
    _, ws = workspace_with_dotgit

    with pytest.raises(PathValidationError, match=r"protected under \.git/"):
        ws.validate_path(".git")


def test_validate_path_blocks_nested_dotgit(workspace_with_dotgit):
    root, ws = workspace_with_dotgit

    (root / ".git" / "refs" / "heads").mkdir(parents=True)
    with pytest.raises(PathValidationError, match=r"protected under \.git/"):
        ws.validate_path(".git/refs/heads/main")


def test_validate_path_blocks_absolute_dotgit(workspace_with_dotgit):
    root, ws = workspace_with_dotgit
    absolute_path = str(root / ".git" / "config")

    with pytest.raises(PathValidationError, match=r"protected under \.git/"):
        ws.validate_path(absolute_path)


def test_read_file_blocks_dotgit(workspace_with_dotgit):
    _, ws = workspace_with_dotgit

    with pytest.raises(PathValidationError, match=r"protected under \.git/"):
        ws.read_file(".git/config")


def test_read_file_lines_blocks_dotgit(workspace_with_dotgit):
    _, ws = workspace_with_dotgit

    with pytest.raises(PathValidationError, match=r"protected under \.git/"):
        ws.read_file_lines(".git/config", 1, 1)


def test_list_files_blocks_dotgit_directory(workspace_with_dotgit):
    _, ws = workspace_with_dotgit

    with pytest.raises(PathValidationError, match=r"protected under \.git/"):
        ws.list_files(".git")


def test_list_files_root_omits_dotgit(workspace_with_dotgit):
    _, ws = workspace_with_dotgit

    files = ws.list_files(".")

    assert ".git" not in files
    assert ".gitignore" in files
    assert "README.md" in files


def test_file_exists_returns_false_for_dotgit(workspace_with_dotgit):
    _, ws = workspace_with_dotgit

    assert ws.file_exists(".git/config") is False


def test_get_absolute_path_blocks_dotgit(workspace_with_dotgit):
    _, ws = workspace_with_dotgit

    with pytest.raises(PathValidationError, match=r"protected under \.git/"):
        ws.get_absolute_path(".git/config")


def test_mutations_under_dotgit_are_blocked(workspace_with_dotgit):
    root, ws = workspace_with_dotgit

    with pytest.raises(PathValidationError, match=r"protected under \.git/"):
        ws.write_file(".git/config", "mutated\n")
    assert (root / ".git" / "config").read_text() == "[user]\n\tname = secret\n"

    with pytest.raises(PathValidationError, match=r"protected under \.git/"):
        ws.delete_file(".git/config")
    assert (root / ".git" / "config").exists()

    with pytest.raises(PathValidationError, match=r"protected under \.git/"):
        ws.create_directory(".git/new_dir")
    assert not (root / ".git" / "new_dir").exists()


def test_dotgitignore_is_not_blocked(workspace_with_dotgit):
    _, ws = workspace_with_dotgit

    assert ws.read_file(".gitignore") == "dist/\n"
    assert ws.file_exists(".gitignore") is True
    assert ws.get_absolute_path(".gitignore").endswith(".gitignore")


def test_mcp_read_file_dotgit_is_wrapped_as_mcp_error(server):
    with pytest.raises(MCPError, match=r"Tool execution failed.*protected under \.git/"):
        server.call_tool("read_file", {"path": ".git/config"})


def test_mcp_list_files_root_omits_dotgit(server):
    result = server.call_tool("list_files", {"directory": "."})

    assert ".git" not in result
    assert "README.md" in result
