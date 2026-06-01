"""Tests for provider registry and detection.

FILE: tests/providers/test_registry.py
"""

import tempfile
import subprocess
from unittest.mock import patch, MagicMock

import pytest

from snodo.providers.base import ProviderError
from snodo.providers.local import LocalProvider
from snodo.providers.registry import (
    detect_provider,
    parse_github_slug,
    _get_git_remote,
    _detect_from_url,
    _load_entry_point,
    list_providers,
)


# === parse_github_slug ===

class TestParseGithubSlug:
    def test_ssh_url(self):
        assert parse_github_slug("git@github.com:owner/repo.git") == "owner/repo"

    def test_https_url_with_git(self):
        assert parse_github_slug("https://github.com/owner/repo.git") == "owner/repo"

    def test_https_url_without_git(self):
        assert parse_github_slug("https://github.com/owner/repo") == "owner/repo"

    def test_non_github_url(self):
        assert parse_github_slug("git@gitlab.com:owner/repo.git") is None

    def test_empty_string(self):
        assert parse_github_slug("") is None

    def test_malformed_url(self):
        assert parse_github_slug("not-a-url") is None


# === _detect_from_url ===

class TestDetectFromUrl:
    def test_github_ssh(self):
        assert _detect_from_url("git@github.com:o/r.git") == "github"

    def test_github_https(self):
        assert _detect_from_url("https://github.com/o/r") == "github"

    def test_gitlab_returns_none(self):
        assert _detect_from_url("git@gitlab.com:o/r.git") is None

    def test_unknown_returns_none(self):
        assert _detect_from_url("https://example.com/o/r") is None


# === _get_git_remote ===

class TestGetGitRemote:
    def test_returns_remote_url(self):
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "https://github.com/test/repo.git"],
            cwd=d, capture_output=True, check=True,
        )

        url = _get_git_remote(d)
        assert url == "https://github.com/test/repo.git"

        import shutil
        shutil.rmtree(d)

    def test_returns_none_no_remote(self):
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)

        url = _get_git_remote(d)
        assert url is None

        import shutil
        shutil.rmtree(d)

    def test_returns_none_not_git_repo(self):
        d = tempfile.mkdtemp()
        url = _get_git_remote(d)
        assert url is None

        import shutil
        shutil.rmtree(d)


# === detect_provider ===

class TestDetectProvider:
    def test_explicit_local_in_metadata(self):
        provider = detect_provider("/tmp", protocol_metadata={"provider": "local"})
        assert isinstance(provider, LocalProvider)

    def test_explicit_unknown_provider_raises(self):
        with pytest.raises(ProviderError, match="Unknown provider"):
            detect_provider("/tmp", protocol_metadata={"provider": "unknown_xyz"})

    def test_fallback_to_local_no_remote(self):
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)

        provider = detect_provider(d)
        assert isinstance(provider, LocalProvider)

        import shutil
        shutil.rmtree(d)

    def test_auto_detect_github(self):
        """GitHub remote triggers GitHubProvider creation (may fail auth)."""
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
        subprocess.run(
            ["git", "remote", "add", "origin", "git@github.com:test/repo.git"],
            cwd=d, capture_output=True, check=True,
        )

        # Without a valid token, GitHub provider will fail to init
        # detect_provider in ProtocolMCPServer catches this and returns None
        # but direct call should raise
        with patch.dict("os.environ", {}, clear=False):
            with patch("snodo.providers.github.GitHubProvider._resolve_token", return_value=None):
                with pytest.raises(ProviderError, match="GitHub token required"):
                    detect_provider(d)

        import shutil
        shutil.rmtree(d)

    def test_explicit_github_in_metadata(self):
        """Explicit github provider with mocked initialization."""
        mock_github = MagicMock()
        mock_github.return_value.get_repo.return_value = MagicMock()

        with patch("snodo.providers.github.Github", mock_github):
            provider = detect_provider(
                "/tmp",
                protocol_metadata={
                    "provider": "github",
                    "github_repo": "owner/repo",
                    "github_token": "ghp_test",
                },
            )

        from snodo.providers.github import GitHubProvider
        assert isinstance(provider, GitHubProvider)


# === Entry points ===

class TestEntryPoints:
    def test_load_entry_point_not_found(self):
        result = _load_entry_point("nonexistent_provider_xyz")
        assert result is None

    def test_list_providers_includes_builtins(self):
        providers = list_providers()
        assert "github" in providers
        assert "local" in providers
