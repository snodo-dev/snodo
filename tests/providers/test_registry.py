"""Tests for provider registry and detection.

FILE: tests/providers/test_registry.py
"""

import subprocess
import tempfile
from unittest.mock import MagicMock, patch

import pytest
from snodo.providers.base import ProviderError
from snodo.providers.local import LocalProvider
from snodo.providers.registry import (
    _detect_from_url,
    _get_git_remote,
    _load_entry_point,
    detect_provider,
    list_providers,
)


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
            with patch("snodo_provider_github.GitHubProvider._resolve_token", return_value=None):
                with pytest.raises(ProviderError, match="GitHub token required"):
                    detect_provider(d)

        import shutil
        shutil.rmtree(d)

    def test_explicit_github_in_metadata(self):
        """Explicit github provider with mocked initialization."""
        mock_github = MagicMock()
        mock_github.return_value.get_repo.return_value = MagicMock()

        with patch("snodo_provider_github.Github", mock_github):
            provider = detect_provider(
                "/tmp",
                protocol_metadata={
                    "provider": "github",
                    "github_repo": "owner/repo",
                    "github_token": "ghp_test",
                },
            )

        from snodo_provider_github import GitHubProvider
        assert isinstance(provider, GitHubProvider)


# === Entry points ===

class TestEntryPoints:
    def test_plugins_do_not_install_into_core_provider_package(self):
        from importlib.metadata import entry_points
        from pathlib import Path
        import snodo_provider_github

        module_path = Path(snodo_provider_github.__file__).resolve()
        assert "snodo" not in module_path.parts[-3:-1]
        assert module_path.parent.name == "snodo_provider_github"

        for ep in entry_points(group="snodo.providers"):
            distribution = ep.dist
            if distribution is None or distribution.files is None:
                continue
            installed_paths = {str(path).replace("\\", "/") for path in distribution.files}
            assert not any(path.startswith("snodo/providers/") for path in installed_paths), (
                f"Provider plugin distribution {distribution.metadata['Name']} installs files "
                "inside the core snodo.providers package"
            )

    def test_load_entry_point_not_found(self):
        result = _load_entry_point("nonexistent_provider_xyz")
        assert result is None

    def test_list_providers_includes_builtins(self):
        providers = list_providers()
        assert "local" in providers

    def test_uninstalled_provider_has_install_hint(self):
        with pytest.raises(ProviderError, match="uv add snodo-provider-not_installed"):
            detect_provider("/tmp", protocol_metadata={"provider": "not_installed"})

    def test_github_provider_is_discovered_as_plugin(self):
        providers = list_providers()
        assert "github" in providers

    def test_fixture_plugin_is_discovered_and_listed(self):
        plugin = type("Plugin", (), {"remote_hosts": ("gitlab.example",)})
        ep = MagicMock(name="gitlab", value="fixture:Plugin")
        ep.name = "gitlab"
        ep.load.return_value = plugin
        with patch("importlib.metadata.entry_points", return_value=[ep]):
            assert "gitlab" in list_providers()
            assert _detect_from_url("git@gitlab.example:group/repo.git") == "gitlab"

    def test_broken_plugin_is_reported_and_explicit_provider_wins(self):
        from snodo.providers.registry import provider_plugin_status
        ep = MagicMock(value="broken:Provider")
        ep.name = "broken"
        ep.load.side_effect = ImportError("missing dependency")
        with patch("importlib.metadata.entry_points", return_value=[ep]):
            assert "missing dependency" in provider_plugin_status()["broken"]["error"]
            with patch("snodo.providers.registry._create_provider", return_value=LocalProvider()) as create:
                result = detect_provider("/tmp", {"provider": "local"})
                create.assert_called_once_with("local", "/tmp", {"provider": "local"})
                assert isinstance(result, LocalProvider)
