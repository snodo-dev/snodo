"""GitHub code-host provider plugin using PyGithub."""

import json
import os
import re
from typing import Optional

from snodo.providers.base import CodeHostProvider, ProviderError

try:
    from github import Github
except ImportError:  # pragma: no cover - dependency is declared by this plugin
    Github = None  # type: ignore[assignment,misc]


class GitHubProvider(CodeHostProvider):
    """Code-host provider for GitHub repositories."""

    def __init__(self, project_root: str = ".", metadata: Optional[dict] = None,
                 repo_slug: Optional[str] = None, token: Optional[str] = None):
        metadata = metadata or {}
        # Preserve the original public GitHubProvider(repo_slug, token=...) API.
        if repo_slug is None and "/" in project_root and not os.path.exists(project_root):
            repo_slug = project_root
        repo_slug = repo_slug or metadata.get("github_repo")
        if not repo_slug:
            remote = _get_git_remote(project_root)
            repo_slug = parse_github_slug(remote) if remote else None
        if not repo_slug:
            raise ProviderError(
                "Could not determine GitHub repo. Set metadata.github_repo "
                "in protocol.yml or add a github.com git remote."
            )
        if Github is None:
            raise ProviderError("PyGithub is required. Install snodo-provider-github.")
        self._token = token or metadata.get("github_token") or self._resolve_token()
        if not self._token:
            raise ProviderError(
                "GitHub token required. Set GITHUB_TOKEN env var "
                "or configure via: snodo config set github <token>"
            )
        self._repo_slug = repo_slug
        try:
            self._github = Github(self._token)
            self._repo = self._github.get_repo(repo_slug)
        except Exception as exc:
            raise ProviderError(f"Failed to connect to GitHub repo '{repo_slug}': {exc}") from exc

    @classmethod
    def claims_remote(cls, url: str) -> bool:
        return _remote_host(url) == "github.com"

    @staticmethod
    def _resolve_token() -> Optional[str]:
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            return token
        try:
            from snodo.config import ConfigManager
            return ConfigManager().get_key("github")
        except Exception:
            return None

    def create_change_request(self, branch: str, title: str, body: str,
                              target_branch: Optional[str] = None) -> str:
        try:
            pr = self._repo.create_pull(title=title, body=body, head=branch,
                                        base=target_branch or self._repo.default_branch)
            return pr.html_url
        except Exception as exc:
            raise ProviderError(f"Failed to create PR: {exc}") from exc

    def read_change_request_diff(self, change_request_id: str) -> str:
        try:
            patches = []
            for file in self._repo.get_pull(int(change_request_id)).get_files():
                header = f"diff --git a/{file.filename} b/{file.filename}"
                patches.append(f"{header}\n{file.patch}" if file.patch else f"{header}\n(binary file)")
            return "\n".join(patches) if patches else "(no changes)"
        except Exception as exc:
            raise ProviderError(f"Failed to read PR diff: {exc}") from exc

    def post_change_request_comment(self, change_request_id: str, comment: str) -> str:
        try:
            return self._repo.get_pull(int(change_request_id)).create_issue_comment(comment).html_url
        except Exception as exc:
            raise ProviderError(f"Failed to post comment: {exc}") from exc

    def approve_change_request(self, change_request_id: str) -> str:
        try:
            self._repo.get_pull(int(change_request_id)).create_review(event="APPROVE")
            return f"Change request {change_request_id} approved"
        except Exception as exc:
            raise ProviderError(f"Failed to approve PR: {exc}") from exc

    def request_change_request_changes(self, change_request_id: str, reason: str) -> str:
        try:
            self._repo.get_pull(int(change_request_id)).create_review(body=reason, event="REQUEST_CHANGES")
            return f"Change request {change_request_id}: changes requested"
        except Exception as exc:
            raise ProviderError(f"Failed to reject PR: {exc}") from exc

    def merge_change_request(self, change_request_id: str) -> str:
        try:
            result = self._repo.get_pull(int(change_request_id)).merge()
            return f"Change request {change_request_id} merged: {result.sha[:8]}"
        except Exception as exc:
            raise ProviderError(f"Failed to merge PR: {exc}") from exc

    def read_change_request_discussion(self, change_request_id: str) -> str:
        try:
            pr = self._repo.get_pull(int(change_request_id))
            return json.dumps({
                "title": pr.title,
                "comments": [{"author": c.user.login, "body": c.body or ""} for c in pr.get_issue_comments()],
                "reviews": [{"author": r.user.login, "body": r.body or "", "state": r.state}
                            for r in pr.get_reviews()],
            })
        except Exception as exc:
            raise ProviderError(f"Failed to read PR comments: {exc}") from exc


def parse_github_slug(url: str) -> Optional[str]:
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    return match.group(1) if match else None


def _remote_host(url: str) -> str:
    from urllib.parse import urlparse
    if "://" in url:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    match = re.match(r"(?:[^@]+@)?([^:]+):", url)
    if match:
        return match.group(1).lower().rstrip(".")
    return ""


def _get_git_remote(project_root: str) -> Optional[str]:
    import subprocess
    try:
        result = subprocess.run(["git", "remote", "get-url", "origin"], cwd=project_root,
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
