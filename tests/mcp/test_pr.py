"""Tests for PR MCP server (provider-based).

FILE: tests/mcp/test_pr.py

Tests cover:
- PrMCP initialization and provider delegation
- All 7 PR operations through provider
- Error wrapping (ProviderError -> PrError)
- Server integration (TOOL_REGISTRY, MODE_TOOL_MAP, mode filtering)
- WF1 enforcement for mutating PR tools
- --from-pr CLI flag
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from snodo.mcp.pr import PrMCP, PrError
from snodo.providers.base import CodeHostProvider, ProviderError


# === Fixtures ===

@pytest.fixture
def temp_dir():
    """Create a temporary directory for PrMCP."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


class StubProvider(CodeHostProvider):
    """Test provider that returns canned responses."""

    def create_pr(self, branch, title, body):
        return "https://example.com/pull/42"

    def read_pr_diff(self, pr_number):
        return f"diff for PR #{pr_number}"

    def post_review_comment(self, pr_number, comment):
        return "comment posted"

    def approve_pr(self, pr_number):
        return f"PR #{pr_number} approved"

    def reject_pr(self, pr_number, reason):
        return f"PR #{pr_number} changes requested"

    def merge_pr(self, pr_number):
        return f"PR #{pr_number} merged"

    def read_pr_comments(self, pr_number):
        return json.dumps({"title": "Test PR", "comments": [], "reviews": []})


class FailingProvider(CodeHostProvider):
    """Test provider that raises ProviderError on every call."""

    def create_pr(self, branch, title, body):
        raise ProviderError("create failed")

    def read_pr_diff(self, pr_number):
        raise ProviderError("diff failed")

    def post_review_comment(self, pr_number, comment):
        raise ProviderError("comment failed")

    def approve_pr(self, pr_number):
        raise ProviderError("approve failed")

    def reject_pr(self, pr_number, reason):
        raise ProviderError("reject failed")

    def merge_pr(self, pr_number):
        raise ProviderError("merge failed")

    def read_pr_comments(self, pr_number):
        raise ProviderError("comments failed")


@pytest.fixture
def pr_mcp(temp_dir):
    """Create a PrMCP with stub provider."""
    return PrMCP(temp_dir, provider=StubProvider())


@pytest.fixture
def failing_pr_mcp(temp_dir):
    """Create a PrMCP with failing provider."""
    return PrMCP(temp_dir, provider=FailingProvider())


# === Initialization ===

class TestPrMCPInit:
    def test_init_with_provider(self, temp_dir):
        provider = StubProvider()
        mcp = PrMCP(temp_dir, provider=provider)
        assert mcp.project_root == Path(temp_dir).resolve()
        assert mcp.provider is provider

    def test_init_without_provider(self, temp_dir):
        mcp = PrMCP(temp_dir)
        with pytest.raises(PrError, match="No code host provider configured"):
            mcp.provider

    def test_init_nonexistent_root_raises(self):
        with pytest.raises(ValueError, match="does not exist"):
            PrMCP("/nonexistent/path/xyz123")

    def test_init_file_as_root_raises(self):
        with tempfile.NamedTemporaryFile() as tmpfile:
            with pytest.raises(ValueError, match="not a directory"):
                PrMCP(tmpfile.name)

    def test_set_provider(self, temp_dir):
        mcp = PrMCP(temp_dir)
        provider = StubProvider()
        mcp.provider = provider
        assert mcp.provider is provider


# === PR Operations (delegation) ===

class TestCreatePr:
    def test_create_pr_delegates(self, pr_mcp):
        result = pr_mcp.create_pr("feature", "Title", "Body")
        assert "pull/42" in result

    def test_create_pr_wraps_error(self, failing_pr_mcp):
        with pytest.raises(PrError, match="create failed"):
            failing_pr_mcp.create_pr("branch", "title", "body")


class TestReadPrDiff:
    def test_read_pr_diff_delegates(self, pr_mcp):
        result = pr_mcp.read_pr_diff(42)
        assert "diff for PR #42" in result

    def test_read_pr_diff_wraps_error(self, failing_pr_mcp):
        with pytest.raises(PrError, match="diff failed"):
            failing_pr_mcp.read_pr_diff(42)


class TestPostReviewComment:
    def test_post_comment_delegates(self, pr_mcp):
        result = pr_mcp.post_review_comment(42, "Looks good!")
        assert "comment posted" in result

    def test_post_comment_wraps_error(self, failing_pr_mcp):
        with pytest.raises(PrError, match="comment failed"):
            failing_pr_mcp.post_review_comment(42, "comment")


class TestApprovePr:
    def test_approve_delegates(self, pr_mcp):
        result = pr_mcp.approve_pr(42)
        assert "approved" in result

    def test_approve_wraps_error(self, failing_pr_mcp):
        with pytest.raises(PrError, match="approve failed"):
            failing_pr_mcp.approve_pr(42)


class TestRejectPr:
    def test_reject_delegates(self, pr_mcp):
        result = pr_mcp.reject_pr(42, "Needs fixing")
        assert "changes requested" in result

    def test_reject_wraps_error(self, failing_pr_mcp):
        with pytest.raises(PrError, match="reject failed"):
            failing_pr_mcp.reject_pr(42, "reason")


class TestMergePr:
    def test_merge_delegates(self, pr_mcp):
        result = pr_mcp.merge_pr(42)
        assert "merged" in result

    def test_merge_wraps_error(self, failing_pr_mcp):
        with pytest.raises(PrError, match="merge failed"):
            failing_pr_mcp.merge_pr(42)


class TestReadPrComments:
    def test_read_comments_delegates(self, pr_mcp):
        result = pr_mcp.read_pr_comments(42)
        data = json.loads(result)
        assert data["title"] == "Test PR"

    def test_read_comments_wraps_error(self, failing_pr_mcp):
        with pytest.raises(PrError, match="comments failed"):
            failing_pr_mcp.read_pr_comments(42)


class TestNoProvider:
    def test_all_operations_fail_without_provider(self, temp_dir):
        mcp = PrMCP(temp_dir)
        with pytest.raises(PrError, match="No code host provider"):
            mcp.create_pr("b", "t", "d")
        with pytest.raises(PrError, match="No code host provider"):
            mcp.read_pr_diff(1)
        with pytest.raises(PrError, match="No code host provider"):
            mcp.post_review_comment(1, "c")
        with pytest.raises(PrError, match="No code host provider"):
            mcp.approve_pr(1)
        with pytest.raises(PrError, match="No code host provider"):
            mcp.reject_pr(1, "r")
        with pytest.raises(PrError, match="No code host provider"):
            mcp.merge_pr(1)
        with pytest.raises(PrError, match="No code host provider"):
            mcp.read_pr_comments(1)


# === Server Integration ===

class TestServerIntegration:
    """Test that PR tools are correctly registered in the MCP server."""

    def test_pr_tools_in_registry(self):
        from snodo.mcp.server import TOOL_REGISTRY
        pr_tools = ["create_pr", "read_pr_diff", "post_review_comment",
                     "approve_pr", "reject_pr", "merge_pr"]
        for tool in pr_tools:
            assert tool in TOOL_REGISTRY, f"{tool} not in TOOL_REGISTRY"

    def test_pr_tools_registry_schemas(self):
        from snodo.mcp.server import TOOL_REGISTRY
        required_keys = {"description", "inputSchema", "requires_token", "mcp", "method"}
        pr_tools = ["create_pr", "read_pr_diff", "post_review_comment",
                     "approve_pr", "reject_pr", "merge_pr"]
        for tool in pr_tools:
            assert required_keys.issubset(TOOL_REGISTRY[tool].keys()), (
                f"TOOL_REGISTRY['{tool}'] missing keys"
            )

    def test_pr_tools_mcp_is_pr(self):
        from snodo.mcp.server import TOOL_REGISTRY
        pr_tools = ["create_pr", "read_pr_diff", "post_review_comment",
                     "approve_pr", "reject_pr", "merge_pr"]
        for tool in pr_tools:
            assert TOOL_REGISTRY[tool]["mcp"] == "pr"

    def test_read_pr_diff_requires_no_token(self):
        from snodo.mcp.server import TOOL_REGISTRY
        assert TOOL_REGISTRY["read_pr_diff"]["requires_token"] is False

    def test_mutating_pr_tools_require_token(self):
        from snodo.mcp.server import TOOL_REGISTRY
        mutating = ["create_pr", "post_review_comment", "approve_pr", "reject_pr", "merge_pr"]
        for tool in mutating:
            assert TOOL_REGISTRY[tool]["requires_token"] is True, (
                f"{tool} should require token"
            )

    def test_pr_in_mode_tool_map(self):
        from snodo.mcp.server import MODE_TOOL_MAP
        assert "pr" in MODE_TOOL_MAP
        pr_tools = MODE_TOOL_MAP["pr"]
        assert "create_pr" in pr_tools
        assert "read_pr_diff" in pr_tools
        assert "post_review_comment" in pr_tools
        assert "approve_pr" in pr_tools
        assert "reject_pr" in pr_tools
        assert "merge_pr" in pr_tools

    def test_mode_tool_map_pr_all_in_registry(self):
        from snodo.mcp.server import TOOL_REGISTRY, MODE_TOOL_MAP
        for tool in MODE_TOOL_MAP["pr"]:
            assert tool in TOOL_REGISTRY


class TestModeFiltering:
    """Test that reviewer mode gets PR tools and producer does not."""

    @pytest.fixture
    def project_dir(self):
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                        cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                        cwd=d, capture_output=True, check=True)
        readme = Path(d) / "README.md"
        readme.write_text("test")
        subprocess.run(["git", "add", "."], cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"],
                        cwd=d, capture_output=True, check=True)
        yield d
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    @pytest.fixture
    def protocol_with_pr(self):
        from snodo.compiler.models import Protocol
        return Protocol(**{
            "protocol_id": "test",
            "name": "Test Protocol",
            "version": "1.0.0",
            "modes": [
                {
                    "mode_id": "producer",
                    "name": "Producer",
                    "tools": ["edit", "test"],
                    "validators": ["security"],
                },
                {
                    "mode_id": "reviewer",
                    "name": "Reviewer",
                    "tools": ["review", "approve", "merge", "pr"],
                    "validators": ["security"],
                },
            ],
            "validators": [
                {
                    "validator_id": "security",
                    "validator_type": "security",
                    "criteria": ["Check security"],
                },
            ],
            "disagreement_policy": "unanimous",
            "initial_mode": "producer",
        })

    def test_reviewer_has_pr_tools(self, protocol_with_pr, project_dir):
        from snodo.mcp.server import ProtocolMCPServer
        server = ProtocolMCPServer(protocol_with_pr, project_dir, mode_id="reviewer")
        tool_names = {t["name"] for t in server.get_tools()}
        assert "create_pr" in tool_names
        assert "read_pr_diff" in tool_names
        assert "post_review_comment" in tool_names
        assert "approve_pr" in tool_names
        assert "reject_pr" in tool_names
        assert "merge_pr" in tool_names

    def test_producer_no_pr_tools(self, protocol_with_pr, project_dir):
        from snodo.mcp.server import ProtocolMCPServer
        server = ProtocolMCPServer(protocol_with_pr, project_dir, mode_id="producer")
        tool_names = {t["name"] for t in server.get_tools()}
        assert "create_pr" not in tool_names
        assert "read_pr_diff" not in tool_names
        assert "approve_pr" not in tool_names
        assert "merge_pr" not in tool_names

    def test_reviewer_pr_tools_wf1_enforced(self, protocol_with_pr, project_dir):
        """Mutating PR tools require validation token."""
        from snodo.mcp.server import ProtocolMCPServer, MCPError
        server = ProtocolMCPServer(protocol_with_pr, project_dir, mode_id="reviewer")

        # Mutating tools should fail without token
        with pytest.raises(MCPError, match="WF1 violation"):
            server.call_tool("create_pr", {
                "branch": "feat", "title": "t", "body": "b"
            })

        with pytest.raises(MCPError, match="WF1 violation"):
            server.call_tool("approve_pr", {"pr_number": 1})

        with pytest.raises(MCPError, match="WF1 violation"):
            server.call_tool("merge_pr", {"pr_number": 1})

    def test_reviewer_read_pr_diff_no_token_needed(self, protocol_with_pr, project_dir):
        """read_pr_diff should work without validation token (but needs provider)."""
        from snodo.mcp.server import ProtocolMCPServer
        server = ProtocolMCPServer(protocol_with_pr, project_dir, mode_id="reviewer")
        # Mock the provider on the pr mcp
        server.pr.provider = StubProvider()
        result = server.call_tool("read_pr_diff", {"pr_number": 42})
        assert "diff for PR #42" in result


class TestDefaultProtocol:
    """Test that the default protocol template includes PR tools for reviewer."""

    def test_default_protocol_reviewer_has_pr(self):
        from snodo.cli.main import DEFAULT_PROTOCOL
        import yaml
        data = yaml.safe_load(DEFAULT_PROTOCOL)
        reviewer = None
        for mode in data["modes"]:
            if mode["mode_id"] == "reviewer":
                reviewer = mode
                break
        assert reviewer is not None
        assert "pr" in reviewer["tools"]

    def test_default_protocol_producer_no_pr(self):
        from snodo.cli.main import DEFAULT_PROTOCOL
        import yaml
        data = yaml.safe_load(DEFAULT_PROTOCOL)
        producer = None
        for mode in data["modes"]:
            if mode["mode_id"] == "producer":
                producer = mode
                break
        assert producer is not None
        assert "pr" not in producer["tools"]


# === _fetch_pr_context ===

class TestFetchPrContext:
    def test_fetch_pr_context_full(self):
        from snodo.cli.main import _fetch_pr_context

        pr_json = json.dumps({
            "title": "Add auth",
            "comments": [
                {"author": {"login": "alice"}, "body": "Nice work!"},
                {"author": {"login": "bob"}, "body": "Add error handling"},
            ],
            "reviews": [
                {"author": {"login": "carol"}, "body": "Needs tests", "state": "CHANGES_REQUESTED"},
            ],
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("snodo.mcp.pr.PrMCP.read_pr_comments", return_value=pr_json):
                with patch("snodo.mcp.pr.PrMCP.read_pr_diff", return_value="diff --git a/f.py"):
                    context = _fetch_pr_context(42, tmpdir)

        assert "PR #42" in context
        assert "PR Title: Add auth" in context
        assert "@alice: Nice work!" in context
        assert "@bob: Add error handling" in context
        assert "@carol [CHANGES_REQUESTED]: Needs tests" in context
        assert "diff --git a/f.py" in context
        assert "--- End PR Context ---" in context

    def test_fetch_pr_context_no_comments(self):
        from snodo.cli.main import _fetch_pr_context

        pr_json = json.dumps({"title": "Empty PR", "comments": [], "reviews": []})

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("snodo.mcp.pr.PrMCP.read_pr_comments", return_value=pr_json):
                with patch("snodo.mcp.pr.PrMCP.read_pr_diff", return_value="diff content"):
                    context = _fetch_pr_context(1, tmpdir)

        assert "PR Title: Empty PR" in context
        assert "Review Comments" not in context
        assert "diff content" in context

    def test_fetch_pr_context_comments_error(self):
        from snodo.cli.main import _fetch_pr_context

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("snodo.mcp.pr.PrMCP.read_pr_comments", side_effect=PrError("no auth")):
                with patch("snodo.mcp.pr.PrMCP.read_pr_diff", return_value="diff"):
                    context = _fetch_pr_context(42, tmpdir)

        assert "Could not fetch PR comments" in context
        assert "diff" in context

    def test_fetch_pr_context_diff_error(self):
        from snodo.cli.main import _fetch_pr_context

        pr_json = json.dumps({"title": "PR", "comments": [], "reviews": []})

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("snodo.mcp.pr.PrMCP.read_pr_comments", return_value=pr_json):
                with patch("snodo.mcp.pr.PrMCP.read_pr_diff", side_effect=PrError("no diff")):
                    context = _fetch_pr_context(42, tmpdir)

        assert "Could not fetch PR diff" in context

    def test_fetch_pr_context_both_errors(self):
        from snodo.cli.main import _fetch_pr_context

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("snodo.mcp.pr.PrMCP.read_pr_comments", side_effect=PrError("err")):
                with patch("snodo.mcp.pr.PrMCP.read_pr_diff", side_effect=PrError("err")):
                    context = _fetch_pr_context(42, tmpdir)

        assert "PR #42" in context
        assert "Could not fetch PR comments" in context
        assert "Could not fetch PR diff" in context

    def test_fetch_pr_context_skips_empty_bodies(self):
        from snodo.cli.main import _fetch_pr_context

        pr_json = json.dumps({
            "title": "PR",
            "comments": [
                {"author": {"login": "x"}, "body": ""},
                {"author": {"login": "y"}, "body": "Real comment"},
            ],
            "reviews": [
                {"author": {"login": "z"}, "body": "", "state": "APPROVED"},
            ],
        })

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("snodo.mcp.pr.PrMCP.read_pr_comments", return_value=pr_json):
                with patch("snodo.mcp.pr.PrMCP.read_pr_diff", return_value=""):
                    context = _fetch_pr_context(1, tmpdir)

        assert "@x" not in context
        assert "@y: Real comment" in context
        assert "@z" not in context


# === CLI --from-pr Integration ===

class TestFromPrCLI:
    @pytest.fixture
    def initialized_project(self):
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                        cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                        cwd=d, capture_output=True, check=True)
        readme = Path(d) / "README.md"
        readme.write_text("test")
        subprocess.run(["git", "add", "."], cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"],
                        cwd=d, capture_output=True, check=True)

        from snodo.cli.main import DEFAULT_PROTOCOL
        snodo_dir = Path(d) / ".snodo"
        snodo_dir.mkdir()
        (snodo_dir / "protocol.yml").write_text(DEFAULT_PROTOCOL + "\n")

        original = os.getcwd()
        os.chdir(d)
        yield d
        os.chdir(original)
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    def test_from_pr_prepends_context(self, initialized_project, capsys):
        """--from-pr fetches PR context and prepends it to description."""
        from snodo.cli.main import main

        pr_json = json.dumps({
            "title": "Fix auth bug",
            "comments": [
                {"author": {"login": "reviewer"}, "body": "Handle null tokens"},
            ],
            "reviews": [],
        })

        with patch("snodo.mcp.pr.PrMCP.read_pr_comments", return_value=pr_json):
            with patch("snodo.mcp.pr.PrMCP.read_pr_diff", return_value="diff content"):
                with patch('sys.argv', ['snodo', 'run', '--from-pr', '42',
                                        'fix the auth bug', '--mock']):
                    result = main()

        # Warn stubs under unanimous → ESCALATE → exit 1
        assert result == 1
        captured = capsys.readouterr()
        assert "Fetching PR #42 context" in captured.out
        assert "PR context prepended" in captured.out

    def test_from_pr_still_works_when_provider_fails(self, initialized_project, capsys):
        """--from-pr degrades gracefully if provider fails."""
        from snodo.cli.main import main

        with patch("snodo.mcp.pr.PrMCP.read_pr_comments", side_effect=PrError("no auth")):
            with patch("snodo.mcp.pr.PrMCP.read_pr_diff", side_effect=PrError("no auth")):
                with patch('sys.argv', ['snodo', 'run', '--from-pr', '99',
                                        'fix the bug', '--mock']):
                    result = main()

        # PR context errors are non-fatal. Warn stubs → ESCALATE → exit 1.
        assert result == 1
