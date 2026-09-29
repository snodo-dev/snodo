"""GitHub code host provider using PyGithub.

FILE: snodo/providers/github.py

Implements CodeHostProvider for GitHub using the PyGithub library.
No gh CLI required.
"""

import json
import os
from typing import Optional

from snodo.providers.base import CodeHostProvider, ProviderError

try:
    from github import Github
except ImportError:
    Github = None  # type: ignore[assignment,misc]


class GitHubProvider(CodeHostProvider):
    """GitHub provider using PyGithub.

    Authentication via GITHUB_TOKEN env var or snodo config.
    Repo slug detected from git remote URL or passed explicitly.
    """

    def __init__(self, repo_slug: str, token: Optional[str] = None):
        """Initialize GitHub provider.

        Args:
            repo_slug: Repository in "owner/repo" format
            token: GitHub API token. If None, resolved from environment.

        Raises:
            ProviderError: If PyGithub is not installed or auth fails
        """
        if Github is None:
            raise ProviderError(
                "PyGithub is required for GitHub provider. "
                "Install it with: pip install PyGithub"
            )

        self._token = token or self._resolve_token()
        if not self._token:
            raise ProviderError(
                "GitHub token required. Set GITHUB_TOKEN env var "
                "or configure via: snodo config set github <token>"
            )

        self._repo_slug = repo_slug

        try:
            self._github = Github(self._token)
            self._repo = self._github.get_repo(repo_slug)
        except Exception as e:
            raise ProviderError(f"Failed to connect to GitHub repo '{repo_slug}': {e}") from e

    @staticmethod
    def _resolve_token() -> Optional[str]:
        """Resolve GitHub token from env or snodo config."""
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            return token
        try:
            from snodo.config import ConfigManager
            return ConfigManager().get_key("github")
        except Exception:
            return None

    def create_change_request(
        self, branch: str, title: str, body: str, target_branch: Optional[str] = None
    ) -> str:
        """Create a change request on GitHub."""
        try:
            pr = self._repo.create_pull(
                title=title, body=body, head=branch,
                base=target_branch or self._repo.default_branch,
            )
            return pr.html_url
        except Exception as e:
            raise ProviderError(f"Failed to create PR: {e}") from e

    def read_change_request_diff(self, change_request_id: str) -> str:
        """Read PR diff by concatenating file patches."""
        try:
            pr = self._repo.get_pull(int(change_request_id))
            files = pr.get_files()
            patches = []
            for f in files:
                header = f"diff --git a/{f.filename} b/{f.filename}"
                if f.patch:
                    patches.append(f"{header}\n{f.patch}")
                else:
                    patches.append(f"{header}\n(binary file)")
            return "\n".join(patches) if patches else "(no changes)"
        except Exception as e:
            raise ProviderError(f"Failed to read PR diff: {e}") from e

    def post_change_request_comment(self, change_request_id: str, comment: str) -> str:
        """Post a comment on a GitHub PR."""
        try:
            pr = self._repo.get_pull(int(change_request_id))
            c = pr.create_issue_comment(comment)
            return c.html_url
        except Exception as e:
            raise ProviderError(f"Failed to post comment: {e}") from e

    def approve_change_request(self, change_request_id: str) -> str:
        """Approve a GitHub PR."""
        try:
            pr = self._repo.get_pull(int(change_request_id))
            pr.create_review(event="APPROVE")
            return f"Change request {change_request_id} approved"
        except Exception as e:
            raise ProviderError(f"Failed to approve PR: {e}") from e

    def request_change_request_changes(self, change_request_id: str, reason: str) -> str:
        """Request changes on a GitHub PR."""
        try:
            pr = self._repo.get_pull(int(change_request_id))
            pr.create_review(body=reason, event="REQUEST_CHANGES")
            return f"Change request {change_request_id}: changes requested"
        except Exception as e:
            raise ProviderError(f"Failed to reject PR: {e}") from e

    def merge_change_request(self, change_request_id: str) -> str:
        """Merge a GitHub PR."""
        try:
            pr = self._repo.get_pull(int(change_request_id))
            result = pr.merge()
            return f"Change request {change_request_id} merged: {result.sha[:8]}"
        except Exception as e:
            raise ProviderError(f"Failed to merge PR: {e}") from e

    def read_change_request_discussion(self, change_request_id: str) -> str:
        """Read comments and reviews in the provider-neutral discussion shape."""
        try:
            pr = self._repo.get_pull(int(change_request_id))
            comments = [
                {"author": c.user.login, "body": c.body or ""}
                for c in pr.get_issue_comments()
            ]
            reviews = [
                {
                    "author": r.user.login,
                    "body": r.body or "",
                    "state": r.state,
                }
                for r in pr.get_reviews()
            ]
            return json.dumps({
                "title": pr.title,
                "comments": comments,
                "reviews": reviews,
            })
        except Exception as e:
            raise ProviderError(f"Failed to read PR comments: {e}") from e
