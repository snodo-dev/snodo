"""Provider registry: detection, resolution, and plugin discovery.

FILE: snodo/providers/registry.py

Resolves which CodeHostProvider to use for a project:
1. Explicit provider in protocol.metadata["provider"]
2. Auto-detect from git remote URL
3. Setuptools entry points (snodo.providers group)
4. Fallback to LocalProvider
"""

import logging
import re
import subprocess
from urllib.parse import urlparse
from typing import Dict, Optional, Type

from snodo.providers.base import CodeHostProvider, ProviderError
from snodo.providers.local import LocalProvider

_logger = logging.getLogger(__name__)


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
            ["git", "remote", "get-url", "origin"],  # noqa: S607 - git resolved from PATH by design; argv list, no shell, fully controlled flags
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
    if "github.com" in _remote_host(url):
        return "github"
    host = _remote_host(url)
    for name, provider_cls in _loaded_plugins().items():
        hosts = getattr(provider_cls, "remote_hosts", ())
        if isinstance(hosts, str):
            hosts = (hosts,)
        if any(host == declared.lower().strip().rstrip(".") for declared in hosts):
            return name
    return None


def _remote_host(url: str) -> str:
    """Extract the host from HTTPS, ssh://, or scp-style git remotes."""
    parsed = urlparse(url if "://" in url else "")
    if parsed.hostname:
        return parsed.hostname.lower().rstrip(".")
    match = re.match(r"(?:[^@]+@)?([^:]+):", url)
    return match.group(1).lower().rstrip(".") if match else ""


def _entry_point_records():
    """Return (entry point, class, error) records for installed plugins."""
    from importlib.metadata import entry_points
    try:
        eps = entry_points(group="snodo.providers")
    except Exception as exc:
        _logger.warning("Could not discover snodo.providers entry points: %s", exc)
        return []
    records = []
    for ep in eps:
        try:
            records.append((ep, ep.load(), None))
        except Exception as exc:
            _logger.warning("Could not load code-host plugin '%s': %s: %s", ep.name, type(exc).__name__, exc)
            records.append((ep, None, exc))
    return records


def _loaded_plugins() -> Dict[str, Type[CodeHostProvider]]:
    return {ep.name: cls for ep, cls, error in _entry_point_records() if cls is not None}


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
            try:
                return provider_cls(project_root=project_root, metadata=metadata)  # type: ignore[call-arg]
            except TypeError:
                # Provider may not accept these kwargs
                return provider_cls()
        except Exception as exc:
            raise ProviderError(
                f"Could not construct code-host plugin '{name}': {type(exc).__name__}: {exc}"
            ) from exc

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
    for ep, provider_cls, error in _entry_point_records():
        if ep.name == name:
            if error is not None:
                raise ProviderError(f"Could not load code-host plugin '{name}': {type(error).__name__}: {error}") from error
            return provider_cls
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

    for ep, provider_cls, error in _entry_point_records():
        providers[ep.name] = (f"Plugin: {ep.value}" if error is None else
                              f"Plugin failed to load: {type(error).__name__}: {error}")

    return providers


def provider_plugin_status() -> Dict[str, Dict[str, str]]:
    """Return installed plugin information, including import/constructor failures."""
    result = {}
    for ep, provider_cls, error in _entry_point_records():
        if error is not None:
            result[ep.name] = {"status": "failed", "error": f"{type(error).__name__}: {error}", "hosts": ""}
            continue
        hosts = getattr(provider_cls, "remote_hosts", ())
        if isinstance(hosts, str):
            hosts = (hosts,)
        result[ep.name] = {"status": "installed", "error": "", "hosts": ", ".join(hosts)}
    return result


def resolve_provider_name(project_root: str, protocol_metadata: Optional[Dict] = None) -> tuple[str, str]:
    """Explain which provider name applies, without constructing the provider."""
    metadata = protocol_metadata or {}
    explicit = metadata.get("provider")
    if explicit:
        return explicit, "selected by metadata.provider"
    remote = _get_git_remote(project_root)
    detected = _detect_from_url(remote) if remote else None
    return (detected, f"detected from remote host {_remote_host(remote)}") if detected else ("local", "no installed provider matches the git remote")
