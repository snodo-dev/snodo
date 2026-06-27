"""Provider registry: detection, resolution, and plugin discovery.

FILE: snodo/providers/registry.py

Resolves which CodeHostProvider to use for a project:
1. Explicit provider in protocol.metadata["provider"]
2. Auto-detect from git remote URL
3. Setuptools entry points (snodo.providers group)
4. Fallback to LocalProvider
"""

import re
import subprocess
from typing import Dict, Optional, Type

from snodo.providers.base import CodeHostProvider, ProviderError
from snodo.providers.local import LocalProvider


# Built-in provider name -> class mapping (lazy imports to avoid hard deps)
_BUILTIN_PROVIDERS = {"github", "local"}


def detect_provider(
    project_root: str,
    protocol_metadata: Optional[Dict] = None,
) -> CodeHostProvider:
    """Detect and create the appropriate code host provider.

    Resolution order:
    1. Explicit "provider" key in protocol metadata
    2. Auto-detect from git remote URL
    3. Fallback to LocalProvider

    Args:
        project_root: Absolute path to project root
        protocol_metadata: Optional protocol.metadata dict

    Returns:
        Configured CodeHostProvider instance
    """
    metadata = protocol_metadata or {}

    # 1. Explicit provider in metadata
    provider_name = metadata.get("provider")
    if provider_name:
        return _create_provider(provider_name, project_root, metadata)

    # 2. Auto-detect from git remote
    remote_url = _get_git_remote(project_root)
    if remote_url:
        detected = _detect_from_url(remote_url)
        if detected:
            return _create_provider(detected, project_root, metadata)

    # 3. Fallback
    return LocalProvider()


def _get_git_remote(project_root: str) -> Optional[str]:
    """Get the origin remote URL from git.

    Returns:
        Remote URL string, or None if not available
    """
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _detect_from_url(url: str) -> Optional[str]:
    """Detect provider name from a git remote URL.

    Args:
        url: Git remote URL (SSH or HTTPS)

    Returns:
        Provider name string, or None if no match
    """
    if "github.com" in url:
        return "github"
    # Future: gitlab.com, bitbucket.org, etc.
    return None


def parse_github_slug(url: str) -> Optional[str]:
    """Extract owner/repo slug from a GitHub remote URL.

    Handles:
    - git@github.com:owner/repo.git
    - https://github.com/owner/repo.git
    - https://github.com/owner/repo

    Args:
        url: Git remote URL

    Returns:
        "owner/repo" string, or None if not a GitHub URL
    """
    match = re.search(r"github\.com[:/]([^/]+/[^/]+?)(?:\.git)?$", url)
    if match:
        return match.group(1)
    return None


def _create_provider(
    name: str,
    project_root: str,
    metadata: Optional[Dict] = None,
) -> CodeHostProvider:
    """Create a provider instance by name.

    Checks built-in providers first, then entry points.

    Args:
        name: Provider name (e.g., "github", "local")
        project_root: Project root directory
        metadata: Protocol metadata for provider config

    Returns:
        CodeHostProvider instance

    Raises:
        ProviderError: If provider not found or initialization fails
    """
    metadata = metadata or {}

    if name == "local":
        return LocalProvider()

    if name == "github":
        return _create_github(project_root, metadata)

    # Check entry points for third-party providers
    provider_cls = _load_entry_point(name)
    if provider_cls:
        try:
            return provider_cls(project_root=project_root, metadata=metadata)  # type: ignore[call-arg]
        except TypeError:
            # Provider may not accept these kwargs
            return provider_cls()

    raise ProviderError(
        f"Unknown provider: '{name}'. "
        f"Built-in providers: {', '.join(sorted(_BUILTIN_PROVIDERS))}. "
        f"Install a plugin or check your protocol metadata."
    )


def _create_github(project_root: str, metadata: Dict) -> CodeHostProvider:
    """Create a GitHubProvider, resolving repo slug from git remote."""
    from snodo.providers.github import GitHubProvider

    # Repo slug from metadata or git remote
    repo_slug = metadata.get("github_repo")
    if not repo_slug:
        remote_url = _get_git_remote(project_root)
        if remote_url:
            repo_slug = parse_github_slug(remote_url)
    if not repo_slug:
        raise ProviderError(
            "Could not determine GitHub repo. Set metadata.github_repo "
            "in protocol.yml or add a github.com git remote."
        )

    token = metadata.get("github_token")
    return GitHubProvider(repo_slug=repo_slug, token=token)


def _load_entry_point(name: str) -> Optional[Type[CodeHostProvider]]:
    """Load a provider class from setuptools entry points.

    Looks in the 'snodo.providers' entry point group.

    Args:
        name: Entry point name

    Returns:
        Provider class, or None if not found
    """
    try:
        from importlib.metadata import entry_points
        eps = entry_points(group="snodo.providers")
        for ep in eps:
            if ep.name == name:
                return ep.load()
    except Exception:
        pass
    return None


def list_providers() -> Dict[str, str]:
    """List all available providers (built-in + plugins).

    Returns:
        Dict of provider_name -> description
    """
    providers = {
        "github": "GitHub (PyGithub)",
        "local": "Local only (no remote)",
    }

    try:
        from importlib.metadata import entry_points
        eps = entry_points(group="snodo.providers")
        for ep in eps:
            providers[ep.name] = f"Plugin: {ep.value}"
    except Exception:
        pass

    return providers
