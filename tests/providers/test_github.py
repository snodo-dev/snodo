"""Tests for GitHubProvider.

FILE: tests/providers/test_github.py

Tests GitHubProvider using mocked PyGithub objects.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

from snodo.providers.base import ProviderError


# === Initialization ===

class TestGitHubProviderInit:
    def test_init_with_token_and_slug(self):
        mock_github = MagicMock()
        mock_repo = MagicMock()
        mock_github.return_value.get_repo.return_value = mock_repo

        with patch("snodo.providers.github.Github", mock_github):
            from snodo.providers.github import GitHubProvider
            provider = GitHubProvider("owner/repo", token="ghp_test123")

        assert provider._repo_slug == "owner/repo"
        mock_github.assert_called_once_with("ghp_test123")
        mock_github.return_value.get_repo.assert_called_once_with("owner/repo")

    def test_init_token_from_env(self):
        mock_github = MagicMock()
        mock_github.return_value.get_repo.return_value = MagicMock()

        with patch("snodo.providers.github.Github", mock_github):
            with patch.dict("os.environ", {"GITHUB_TOKEN": "ghp_env"}):
                from snodo.providers.github import GitHubProvider
                provider = GitHubProvider("owner/repo")

        mock_github.assert_called_once_with("ghp_env")

    def test_init_no_token_raises(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("snodo.providers.github.GitHubProvider._resolve_token", return_value=None):
                from snodo.providers.github import GitHubProvider
                with pytest.raises(ProviderError, match="GitHub token required"):
                    GitHubProvider("owner/repo")

    def test_init_bad_repo_raises(self):
        mock_github = MagicMock()
        mock_github.return_value.get_repo.side_effect = Exception("Not Found")

        with patch("snodo.providers.github.Github", mock_github):
            from snodo.providers.github import GitHubProvider
            with pytest.raises(ProviderError, match="Failed to connect"):
                GitHubProvider("bad/repo", token="ghp_test")


# === Fixtures ===

@pytest.fixture
def github_provider():
    """Create a GitHubProvider with fully mocked PyGithub."""
    mock_github_cls = MagicMock()
    mock_repo = MagicMock()
    mock_github_cls.return_value.get_repo.return_value = mock_repo

    with patch("snodo.providers.github.Github", mock_github_cls):
        from snodo.providers.github import GitHubProvider
        provider = GitHubProvider("owner/repo", token="ghp_test")

    # Expose mock_repo for assertions
    provider._mock_repo = mock_repo
    return provider


# === PR Operations ===

class TestCreatePr:
    def test_create_pr_success(self, github_provider):
        mock_pr = MagicMock()
        mock_pr.html_url = "https://github.com/owner/repo/pull/42"
        github_provider._mock_repo.create_pull.return_value = mock_pr

        result = github_provider.create_pr("feature", "Add feature", "Description")

        assert result == "https://github.com/owner/repo/pull/42"
        github_provider._mock_repo.create_pull.assert_called_once_with(
            title="Add feature", body="Description", head="feature", base="main",
        )

    def test_create_pr_failure(self, github_provider):
        github_provider._mock_repo.create_pull.side_effect = Exception("conflict")

        with pytest.raises(ProviderError, match="Failed to create PR"):
            github_provider.create_pr("branch", "title", "body")


class TestReadPrDiff:
    def test_read_pr_diff_with_patches(self, github_provider):
        mock_pr = MagicMock()
        file1 = MagicMock(filename="src/main.py", patch="@@ -1 +1 @@\n-old\n+new")
        file2 = MagicMock(filename="README.md", patch="@@ -1 +1 @@\n-readme\n+updated")
        mock_pr.get_files.return_value = [file1, file2]
        github_provider._mock_repo.get_pull.return_value = mock_pr

        result = github_provider.read_pr_diff(42)

        assert "diff --git a/src/main.py b/src/main.py" in result
        assert "-old" in result
        assert "+new" in result
        assert "diff --git a/README.md b/README.md" in result

    def test_read_pr_diff_binary_file(self, github_provider):
        mock_pr = MagicMock()
        file1 = MagicMock(filename="image.png", patch=None)
        mock_pr.get_files.return_value = [file1]
        github_provider._mock_repo.get_pull.return_value = mock_pr

        result = github_provider.read_pr_diff(1)

        assert "(binary file)" in result

    def test_read_pr_diff_no_changes(self, github_provider):
        mock_pr = MagicMock()
        mock_pr.get_files.return_value = []
        github_provider._mock_repo.get_pull.return_value = mock_pr

        result = github_provider.read_pr_diff(1)

        assert "(no changes)" in result

    def test_read_pr_diff_failure(self, github_provider):
        github_provider._mock_repo.get_pull.side_effect = Exception("not found")

        with pytest.raises(ProviderError, match="Failed to read PR diff"):
            github_provider.read_pr_diff(999)


class TestPostReviewComment:
    def test_post_comment_success(self, github_provider):
        mock_pr = MagicMock()
        mock_comment = MagicMock()
        mock_comment.html_url = "https://github.com/owner/repo/pull/42#comment-1"
        mock_pr.create_issue_comment.return_value = mock_comment
        github_provider._mock_repo.get_pull.return_value = mock_pr

        result = github_provider.post_review_comment(42, "Looks good!")

        assert "comment-1" in result
        mock_pr.create_issue_comment.assert_called_once_with("Looks good!")

    def test_post_comment_failure(self, github_provider):
        github_provider._mock_repo.get_pull.side_effect = Exception("forbidden")

        with pytest.raises(ProviderError, match="Failed to post comment"):
            github_provider.post_review_comment(42, "comment")


class TestApprovePr:
    def test_approve_success(self, github_provider):
        mock_pr = MagicMock()
        github_provider._mock_repo.get_pull.return_value = mock_pr

        result = github_provider.approve_pr(42)

        assert "approved" in result
        mock_pr.create_review.assert_called_once_with(event="APPROVE")

    def test_approve_failure(self, github_provider):
        mock_pr = MagicMock()
        mock_pr.create_review.side_effect = Exception("forbidden")
        github_provider._mock_repo.get_pull.return_value = mock_pr

        with pytest.raises(ProviderError, match="Failed to approve"):
            github_provider.approve_pr(42)


class TestRejectPr:
    def test_reject_success(self, github_provider):
        mock_pr = MagicMock()
        github_provider._mock_repo.get_pull.return_value = mock_pr

        result = github_provider.reject_pr(42, "Needs fixing")

        assert "changes requested" in result
        mock_pr.create_review.assert_called_once_with(
            body="Needs fixing", event="REQUEST_CHANGES",
        )

    def test_reject_failure(self, github_provider):
        mock_pr = MagicMock()
        mock_pr.create_review.side_effect = Exception("error")
        github_provider._mock_repo.get_pull.return_value = mock_pr

        with pytest.raises(ProviderError, match="Failed to reject"):
            github_provider.reject_pr(42, "reason")


class TestMergePr:
    def test_merge_success(self, github_provider):
        mock_pr = MagicMock()
        mock_result = MagicMock(sha="abc12345def")
        mock_pr.merge.return_value = mock_result
        github_provider._mock_repo.get_pull.return_value = mock_pr

        result = github_provider.merge_pr(42)

        assert "merged" in result
        assert "abc12345" in result

    def test_merge_failure(self, github_provider):
        mock_pr = MagicMock()
        mock_pr.merge.side_effect = Exception("merge conflict")
        github_provider._mock_repo.get_pull.return_value = mock_pr

        with pytest.raises(ProviderError, match="Failed to merge"):
            github_provider.merge_pr(42)


class TestReadPrComments:
    def test_read_comments_success(self, github_provider):
        mock_pr = MagicMock()
        mock_pr.title = "Fix bug"

        comment1 = MagicMock()
        comment1.user.login = "alice"
        comment1.body = "Looks good"

        review1 = MagicMock()
        review1.user.login = "bob"
        review1.body = "Needs tests"
        review1.state = "CHANGES_REQUESTED"

        mock_pr.get_issue_comments.return_value = [comment1]
        mock_pr.get_reviews.return_value = [review1]
        github_provider._mock_repo.get_pull.return_value = mock_pr

        result = github_provider.read_pr_comments(42)
        data = json.loads(result)

        assert data["title"] == "Fix bug"
        assert len(data["comments"]) == 1
        assert data["comments"][0]["author"]["login"] == "alice"
        assert data["comments"][0]["body"] == "Looks good"
        assert len(data["reviews"]) == 1
        assert data["reviews"][0]["state"] == "CHANGES_REQUESTED"

    def test_read_comments_failure(self, github_provider):
        github_provider._mock_repo.get_pull.side_effect = Exception("not found")

        with pytest.raises(ProviderError, match="Failed to read PR comments"):
            github_provider.read_pr_comments(999)
