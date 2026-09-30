"""GitLab merge-request provider plugin."""

import json
import logging
import os
import re
import subprocess
from typing import Optional
from urllib.parse import urlparse

from snodo.providers.base import CodeHostProvider, ProviderError

logger = logging.getLogger(__name__)

try:
    import gitlab
except ImportError:  # pragma: no cover - dependency is declared by this plugin
    gitlab = None


class GitLabProvider(CodeHostProvider):
    """Code-host provider backed by GitLab merge requests."""

    def __init__(self, project_root: str = ".", metadata: Optional[dict] = None,
                 repo_path: Optional[str] = None, token: Optional[str] = None):
        metadata = metadata or {}
        self._url = _normalize_url(metadata.get("gitlab_url") or metadata.get("gitlab_host") or "https://gitlab.com")
        repo_path = repo_path or metadata.get("gitlab_repo")
        if not repo_path:
            remote = _get_git_remote(project_root)
            repo_path = parse_gitlab_path(remote, self._url) if remote else None
        if not repo_path:
            raise ProviderError("Could not determine GitLab project. Set metadata.gitlab_repo or add a GitLab git remote.")
        if gitlab is None:
            raise ProviderError("python-gitlab is required. Install snodo-provider-gitlab.")
        self._token = token or metadata.get("gitlab_token") or os.environ.get("GITLAB_TOKEN")
        if not self._token:
            raise ProviderError("GitLab token required. Set metadata.gitlab_token or the GITLAB_TOKEN environment variable.")
        try:
            self._client = gitlab.Gitlab(self._url, private_token=self._token)
            self._project = self._client.projects.get(repo_path)
        except Exception as exc:
            raise ProviderError(f"Failed to connect to GitLab project '{repo_path}': {exc}") from exc

    @classmethod
    def claims_remote(cls, url: str) -> bool:
        host = _remote_host(url)
        return host == "gitlab.com" or host in _configured_hosts()

    def create_change_request(self, branch: str, title: str, body: str,
                              target_branch: Optional[str] = None) -> str:
        try:
            mr = self._project.mergerequests.create({
                "source_branch": branch, "target_branch": target_branch or self._project.default_branch,
                "title": title, "description": body,
            })
            reference = str(mr.iid)
            try:
                try:
                    self._project.labels.get("snodo")
                except Exception:
                    self._project.labels.create({"name": "snodo", "color": "#6f42c1"})
                labels = list(getattr(mr, "labels", []) or [])
                if "snodo" not in labels:
                    labels.append("snodo")
                    mr.labels = labels
                    mr.save()
            except Exception:
                logger.warning("Could not apply the snodo label to merge request %s", reference,
                               exc_info=True)
            return reference
        except Exception as exc:
            raise ProviderError(f"Failed to create merge request: {exc}") from exc

    def read_change_request_diff(self, change_request_id: str) -> str:
        try:
            changes = self._mr(change_request_id).changes().get("changes", [])
            return "\n".join(
                f"diff --git a/{item['old_path']} b/{item['new_path']}\n{item.get('diff') or '(binary file)'}"
                for item in changes
            ) or "(no changes)"
        except Exception as exc:
            raise ProviderError(f"Failed to read merge request diff: {exc}") from exc

    def post_change_request_comment(self, change_request_id: str, comment: str) -> str:
        try:
            note = self._mr(change_request_id).discussions.create({"body": comment})
            return str(getattr(note, "id", ""))
        except Exception as exc:
            raise ProviderError(f"Failed to post merge request comment: {exc}") from exc

    def approve_change_request(self, change_request_id: str) -> str:
        try:
            self._mr(change_request_id).approve()
            return f"Change request {change_request_id} approved"
        except Exception as exc:
            raise ProviderError(f"Failed to approve merge request: {exc}") from exc

    def request_change_request_changes(self, change_request_id: str, reason: str) -> str:
        try:
            mr = self._mr(change_request_id)
            mr.discussions.create({"body": reason})
            mr.unapprove()
            return f"Change request {change_request_id}: changes requested"
        except Exception as exc:
            raise ProviderError(f"Failed to request merge request changes: {exc}") from exc

    def merge_change_request(self, change_request_id: str) -> str:
        try:
            result = self._mr(change_request_id).merge()
            return f"Change request {change_request_id} merged: {str(result.get('sha', ''))[:8]}"
        except Exception as exc:
            raise ProviderError(f"Failed to merge merge request: {exc}") from exc

    def read_change_request_discussion(self, change_request_id: str) -> str:
        try:
            mr = self._mr(change_request_id)
            comments = []
            for discussion in mr.discussions.list(all=True):
                for note in getattr(discussion, "attributes", {}).get("notes", []):
                    if note.get("system"):
                        continue
                    comments.append({"author": (note.get("author") or {}).get("username", ""),
                                     "body": note.get("body", "")})
            return json.dumps({"title": mr.title, "comments": comments, "reviews": []})
        except Exception as exc:
            raise ProviderError(f"Failed to read merge request discussion: {exc}") from exc

    def _mr(self, change_request_id: str):
        return self._project.mergerequests.get(int(change_request_id))


def _normalize_url(url: str) -> str:
    return url.rstrip("/")


def _remote_host(url: str) -> str:
    if "://" in url:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    match = re.match(r"(?:[^@]+@)?([^:]+):", url)
    return match.group(1).lower().rstrip(".") if match else ""


def _configured_hosts() -> set[str]:
    # claims_remote has no protocol instance; the conventional env declaration
    # permits registry detection for self-hosted GitLab installations.
    host = os.environ.get("GITLAB_HOST") or os.environ.get("GITLAB_URL", "")
    return {urlparse(_normalize_url(host if "://" in host else f"https://{host}")).hostname or ""} if host else set()


def parse_gitlab_path(remote: str, base_url: str = "https://gitlab.com") -> Optional[str]:
    host = _remote_host(remote)
    if host != (urlparse(base_url).hostname or "").lower():
        return None
    if "://" in remote:
        path = urlparse(remote).path.lstrip("/")
    else:
        match = re.match(r"(?:[^@]+@)?[^:]+:(.+)$", remote)
        path = match.group(1) if match else ""
    return re.sub(r"\.git$", "", path).strip("/") or None


def _get_git_remote(project_root: str) -> Optional[str]:
    try:
        result = subprocess.run(["git", "remote", "get-url", "origin"],  # noqa: S603, S607 - fixed git argv, no shell; git resolved from PATH by design
                                cwd=project_root,
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
