"""Code host provider plugins.

FILE: snodo/providers/__init__.py
"""

from pkgutil import extend_path

# Provider modules may be supplied by separately installed distributions.
__path__ = extend_path(__path__, __name__)

from snodo.providers.base import (
    CODE_HOST_PROVIDER_INTERFACE_VERSION,
    CodeHostProvider,
    ProviderError,
)
from snodo.providers.local import LocalProvider
from snodo.providers.registry import detect_provider, list_providers

__all__ = [
    "CodeHostProvider",
    "CODE_HOST_PROVIDER_INTERFACE_VERSION",
    "ProviderError",
    "LocalProvider",
    "detect_provider",
    "list_providers",
]
