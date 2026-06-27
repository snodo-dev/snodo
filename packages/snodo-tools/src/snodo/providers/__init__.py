"""Code host provider plugins.

FILE: snodo/providers/__init__.py
"""

from snodo.providers.base import CodeHostProvider, ProviderError
from snodo.providers.local import LocalProvider
from snodo.providers.registry import detect_provider, list_providers

__all__ = [
    "CodeHostProvider",
    "ProviderError",
    "LocalProvider",
    "detect_provider",
    "list_providers",
]
