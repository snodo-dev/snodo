"""Tests for Git MCP server.

FILE: tests/mcp/test_git.py

Tests cover:
- Git operations (create_branch, stage_files, commit, read_diff, get_status)
- Path validation and security
- Error handling
- 100% coverage
"""

import pytest
import tempfile
from pathlib import Path
import subprocess

from snodo.mcp.git import GitMCP, GitError, PathValidationError, get_git


@pytest.fixture
def temp_git_repo():
    """Create a temporary git repository for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Initialize git repo
        subprocess.run(["git", "init"], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=tmpdir,
            check=True,
            capture_output=True
        )
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=tmpdir,
            check=True,
            capture_output=True
        )
        
        # Create initial commit
        test_file = Path(tmpdir) / "README.md"
        test_file.write_text("# Test Repo")
        subprocess.run(["git", "add", "README.md"], cwd=tmpdir, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=tmpdir,
            check=True,
            capture_output=True
        )
        
        git_mcp = GitMCP(tmpdir)
        yield git_mcp, tmpdir


# ========== INITIALIZATION TESTS ==========

def test_git_init_with_valid_root(temp_git_repo):
    """Test initializing GitMCP with valid root."""
    git_mcp, tmpdir = temp_git_repo
    assert git_mcp.project_root == Path(tmpdir).resolve()


def test_git_init_nonexistent_root_raises():
    """Test initializing with nonexistent root raises."""
    with pytest.raises(ValueError, match="does not exist"):
        GitMCP("/nonexistent/path/xyz123")


def test_git_init_file_as_root_raises():
    """Test initializing with file as root raises."""
    with tempfile.NamedTemporaryFile() as tmpfile:
        with pytest.raises(ValueError, match="not a directory"):
            GitMCP(tmpfile.name)


def test_git_init_not_git_repo_raises():
    """Test initializing with non-git directory raises."""
    with tempfile.TemporaryDirectory() as tmpdir:
        with pytest.raises(ValueError, match="Not a git repository"):
            GitMCP(tmpdir)


# ========== PATH VALIDATION TESTS ==========

def test_validate_path_relative(temp_git_repo):
    """Test validating relative path."""
    git_mcp, tmpdir = temp_git_repo
    path = git_mcp.validate_path("test.txt")
    
    assert path.is_relative_to(git_mcp.project_root)
    assert path.name == "test.txt"


def test_validate_path_nested_relative(temp_git_repo):
    """Test validating nested relative path."""
    git_mcp, tmpdir = temp_git_repo
    path = git_mcp.validate_path("subdir/file.txt")
    
    assert path.is_relative_to(git_mcp.project_root)
    assert "subdir" in path.parts


def test_validate_path_absolute_within_root(temp_git_repo):
    """Test validating absolute path within root."""
    git_mcp, tmpdir = temp_git_repo
    abs_path = git_mcp.project_root / "test.txt"
    validated = git_mcp.validate_path(str(abs_path))
    
    assert validated == abs_path


def test_validate_path_traversal_blocked(temp_git_repo):
    """Test that ../ path traversal is blocked."""
    git_mcp, _ = temp_git_repo
    with pytest.raises(PathValidationError, match="escapes project root"):
        git_mcp.validate_path("../outside.txt")


def test_validate_path_deep_traversal_blocked(temp_git_repo):
    """Test that ../../ deep traversal is blocked."""
    git_mcp, _ = temp_git_repo
    with pytest.raises(PathValidationError, match="escapes project root"):
        git_mcp.validate_path("../../etc/passwd")


def test_validate_path_absolute_outside_blocked(temp_git_repo):
    """Test that absolute path outside root is blocked."""
    git_mcp, _ = temp_git_repo
    with pytest.raises(PathValidationError, match="escapes project root"):
        git_mcp.validate_path("/etc/passwd")


# ========== CREATE BRANCH TESTS ==========

def test_create_branch(temp_git_repo):
    """Test creating a new branch."""
    git_mcp, tmpdir = temp_git_repo
    result = git_mcp.create_branch("feature-test")
    
    assert "feature-test" in result or result == ""
    
    # Verify branch was created
    branches = subprocess.run(
        ["git", "branch"],
        cwd=tmpdir,
        capture_output=True,
        text=True,
        check=True
    ).stdout
    assert "feature-test" in branches


def test_create_branch_already_exists_raises(temp_git_repo):
    """Test creating existing branch raises."""
    git_mcp, _ = temp_git_repo
    git_mcp.create_branch("existing")
    
    with pytest.raises(GitError):
        git_mcp.create_branch("existing")


# ========== STAGE FILES TESTS ==========

def test_stage_files(temp_git_repo):
    """Test staging files."""
    git_mcp, tmpdir = temp_git_repo
    
    # Create a new file
    test_file = Path(tmpdir) / "new_file.txt"
    test_file.write_text("new content")
    
    result = git_mcp.stage_files(["new_file.txt"])
    
    # Verify file was staged
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmpdir,
        capture_output=True,
        text=True,
        check=True
    ).stdout
    assert "A  new_file.txt" in status or "new_file.txt" in status


def test_stage_files_multiple(temp_git_repo):
    """Test staging multiple files."""
    git_mcp, tmpdir = temp_git_repo
    
    # Create multiple files
    for i in range(3):
        test_file = Path(tmpdir) / f"file{i}.txt"
        test_file.write_text(f"content {i}")
    
    git_mcp.stage_files([f"file{i}.txt" for i in range(3)])
    
    # Verify all files staged
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=tmpdir,
        capture_output=True,
        text=True,
        check=True
    ).stdout
    for i in range(3):
        assert f"file{i}.txt" in status


def test_stage_files_empty_list(temp_git_repo):
    """Test staging empty list."""
    git_mcp, _ = temp_git_repo
    result = git_mcp.stage_files([])
    assert result == ""


def test_stage_files_path_validation(temp_git_repo):
    """Test staging files validates paths."""
    git_mcp, _ = temp_git_repo
    
    with pytest.raises(PathValidationError):
        git_mcp.stage_files(["../outside.txt"])


def test_stage_nonexistent_file_raises(temp_git_repo):
    """Test staging nonexistent file raises."""
    git_mcp, _ = temp_git_repo
    
    with pytest.raises(GitError):
        git_mcp.stage_files(["nonexistent.txt"])


# ========== COMMIT TESTS ==========

def test_commit(temp_git_repo):
    """Test creating a commit."""
    git_mcp, tmpdir = temp_git_repo
    
    # Create and stage a file
    test_file = Path(tmpdir) / "commit_test.txt"
    test_file.write_text("commit content")
    git_mcp.stage_files(["commit_test.txt"])
    
    result = git_mcp.commit("Test commit message")
    
    assert "commit_test.txt" in result or "Test commit message" in result


def test_commit_nothing_to_commit_raises(temp_git_repo):
    """Test committing with nothing staged raises."""
    git_mcp, _ = temp_git_repo
    
    with pytest.raises(GitError):
        git_mcp.commit("Empty commit")


def test_commit_with_multiline_message(temp_git_repo):
    """Test commit with multiline message."""
    git_mcp, tmpdir = temp_git_repo
    
    # Create and stage a file
    test_file = Path(tmpdir) / "multiline.txt"
    test_file.write_text("content")
    git_mcp.stage_files(["multiline.txt"])
    
    message = "Title\n\nBody line 1\nBody line 2"
    result = git_mcp.commit(message)
    
    # Verify commit created
    log = subprocess.run(
        ["git", "log", "-1", "--pretty=format:%B"],
        cwd=tmpdir,
        capture_output=True,
        text=True,
        check=True
    ).stdout
    assert "Title" in log


# ========== READ DIFF TESTS ==========

def test_read_diff_no_changes(temp_git_repo):
    """Test reading diff with no changes."""
    git_mcp, _ = temp_git_repo
    diff = git_mcp.read_diff()
    assert diff == ""


def test_read_diff_with_changes(temp_git_repo):
    """Test reading diff with changes."""
    git_mcp, tmpdir = temp_git_repo
    
    # Modify a file
    test_file = Path(tmpdir) / "README.md"
    test_file.write_text("# Modified content")
    
    diff = git_mcp.read_diff()
    
    assert "README.md" in diff
    assert "Modified content" in diff or "-# Test Repo" in diff


def test_read_diff_new_file(temp_git_repo):
    """Test diff shows new file."""
    git_mcp, tmpdir = temp_git_repo
    
    # Create new file
    test_file = Path(tmpdir) / "new.txt"
    test_file.write_text("new content")
    
    diff = git_mcp.read_diff()
    
    # Untracked files don't show in diff, only after staging
    git_mcp.stage_files(["new.txt"])
    diff = git_mcp.read_diff()
    assert "new.txt" in diff


# ========== GET STATUS TESTS ==========

def test_get_status_clean(temp_git_repo):
    """Test status on clean repo."""
    git_mcp, _ = temp_git_repo
    status = git_mcp.get_status()
    
    assert "nothing to commit" in status or "working tree clean" in status


def test_get_status_with_changes(temp_git_repo):
    """Test status with changes."""
    git_mcp, tmpdir = temp_git_repo
    
    # Modify file
    test_file = Path(tmpdir) / "README.md"
    test_file.write_text("modified")
    
    status = git_mcp.get_status()
    
    assert "README.md" in status
    assert "modified" in status.lower()


def test_get_status_untracked_files(temp_git_repo):
    """Test status shows untracked files."""
    git_mcp, tmpdir = temp_git_repo
    
    # Create new file
    test_file = Path(tmpdir) / "untracked.txt"
    test_file.write_text("untracked")
    
    status = git_mcp.get_status()
    
    assert "untracked.txt" in status
    assert "untracked" in status.lower()


# ========== MERGE BRANCH TESTS ==========

def test_merge_branch(temp_git_repo):
    """Test merging a branch into main."""
    git_mcp, tmpdir = temp_git_repo

    # Create a branch and add a commit
    git_mcp.create_branch("feature-merge")
    test_file = Path(tmpdir) / "merge_file.txt"
    test_file.write_text("merge content")
    git_mcp.stage_files(["merge_file.txt"])
    git_mcp.commit("Add merge file")

    # Merge back to main
    result = git_mcp.merge_branch("feature-merge")

    # Verify we're on main and the file exists
    branch_output = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=tmpdir, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert branch_output == "main"
    assert test_file.exists()


def test_merge_branch_conflict(temp_git_repo):
    """Test merge conflict raises GitError."""
    git_mcp, tmpdir = temp_git_repo

    # Create divergent branches
    git_mcp.create_branch("conflict-branch")
    conflict_file = Path(tmpdir) / "README.md"
    conflict_file.write_text("branch content")
    git_mcp.stage_files(["README.md"])
    git_mcp.commit("Branch change")

    # Go back to main and make a conflicting change
    subprocess.run(["git", "checkout", "main"], cwd=tmpdir, check=True, capture_output=True)
    conflict_file.write_text("main content")
    git_mcp.stage_files(["README.md"])
    git_mcp.commit("Main change")

    with pytest.raises(GitError):
        git_mcp.merge_branch("conflict-branch")


def test_merge_branch_nonexistent(temp_git_repo):
    """Test merging a nonexistent branch raises GitError."""
    git_mcp, _ = temp_git_repo

    with pytest.raises(GitError):
        git_mcp.merge_branch("nonexistent-branch")


# ========== DELETE BRANCH TESTS ==========

def test_delete_branch(temp_git_repo):
    """Test deleting a branch."""
    git_mcp, tmpdir = temp_git_repo

    # Create and switch back from a branch
    git_mcp.create_branch("to-delete")
    subprocess.run(["git", "checkout", "main"], cwd=tmpdir, check=True, capture_output=True)

    result = git_mcp.delete_branch("to-delete")

    # Verify branch is gone
    branches = subprocess.run(
        ["git", "branch"],
        cwd=tmpdir, capture_output=True, text=True, check=True
    ).stdout
    assert "to-delete" not in branches


def test_delete_branch_current(temp_git_repo):
    """Test deleting current branch raises GitError."""
    git_mcp, _ = temp_git_repo

    git_mcp.create_branch("current-branch")
    # We're now on current-branch

    with pytest.raises(GitError):
        git_mcp.delete_branch("current-branch")


def test_delete_branch_nonexistent(temp_git_repo):
    """Test deleting nonexistent branch raises GitError."""
    git_mcp, _ = temp_git_repo

    with pytest.raises(GitError):
        git_mcp.delete_branch("nonexistent-branch")


# ========== ERROR HANDLING TESTS ==========

def test_git_error_message_from_command(temp_git_repo):
    """Test that GitCommandError is wrapped with message."""
    git_mcp, _ = temp_git_repo

    with pytest.raises(GitError, match="Git command failed"):
        git_mcp.create_branch("main")  # branch 'main' already exists


# ========== GLOBAL INSTANCE TESTS ==========

def test_get_git_initializes(temp_git_repo):
    """Test get_git initializes instance."""
    _, tmpdir = temp_git_repo
    git = get_git(tmpdir)
    
    assert isinstance(git, GitMCP)
    assert git.project_root == Path(tmpdir).resolve()


def test_get_git_reuses_instance(temp_git_repo):
    """Test get_git returns same instance."""
    _, tmpdir = temp_git_repo
    git1 = get_git(tmpdir)
    git2 = get_git()
    
    assert git1 is git2


def test_get_git_no_init_raises():
    """Test get_git without init raises."""
    # Reset global instance
    import snodo.mcp.git as git_module
    git_module._git_instance = None
    
    with pytest.raises(ValueError, match="not initialized"):
        get_git()


# ========== INTEGRATION TEST ==========

def test_complete_workflow(temp_git_repo):
    """Test complete git workflow."""
    git_mcp, tmpdir = temp_git_repo
    
    # Create branch
    git_mcp.create_branch("feature-workflow")
    
    # Create and stage file
    test_file = Path(tmpdir) / "workflow.txt"
    test_file.write_text("workflow content")
    git_mcp.stage_files(["workflow.txt"])
    
    # Check status
    status = git_mcp.get_status()
    assert "workflow.txt" in status
    
    # Commit
    git_mcp.commit("Add workflow file")
    
    # Verify clean status
    status = git_mcp.get_status()
    assert "nothing to commit" in status or "working tree clean" in status
