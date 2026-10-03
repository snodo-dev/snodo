"""Provider registry: detection, resolution, and plugin discovery.

FILE: snodo/providers/registry.py

Resolves which CodeHostProvider to use for a project:
1. Explicit provider in protocol.metadata["provider"]
2. Auto-detect from git remote URL using installed plugin claims_remote hooks
   (with remote_hosts as a compatibility fallback)
3. Fallback to LocalProvider (the only built-in provider)
"""

import logging
import re
import subprocess
from urllib.parse import urlparse
from typing import Dict, Optional, Type

from snodo.paths import subprocess_env_without_job_context

from snodo.providers.base import CodeHostProvider, ProviderError
from snodo.providers.local import LocalProvider

_logger = logging.getLogger(__name__)


_BUILTIN_PROVIDERS = {"local"}


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
            env=subprocess_env_without_job_context(),
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
    for name, provider_cls in _loaded_plugins().items():
        claims_remote = getattr(provider_cls, "claims_remote", None)
        if callable(claims_remote) and claims_remote(url):
            return name
        # Compatibility with plugins that declared hosts before claims_remote
        hosts = getattr(provider_cls, "remote_hosts", ())
        if isinstance(hosts, str):
            hosts = (hosts,)
        host = _remote_host(url)
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
        f"Unknown provider: '{name}'. Install its plugin with "
        f"'uv add snodo-provider-{name}' (or 'pip install snodo-provider-{name}'), "
        f"or check your protocol metadata. Built-in providers: local."
    )


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
    providers = {"local": "Local only (no remote)"}

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
