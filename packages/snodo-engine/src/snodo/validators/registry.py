"""Validator registry — validator_type → ValidatorBase class mapping.

FILE: snodo/validators/registry.py (Task 7.20)

Mirrors the PredicateRegistry pattern from 7.8.  Module-level default
singleton with self-registration by each validator module on import.
"""

import logging
from typing import Dict, List, Optional, Type

from snodo.validators.context import ValidatorBase

_logger = logging.getLogger(__name__)


class ValidatorRegistry:
    """Maps validator_type strings to ValidatorBase subclasses."""

    def __init__(self) -> None:
        self._registry: Dict[str, Type[ValidatorBase]] = {}
        self._compound: Dict[str, str] = {}  # type → primary key
        self._plugin_load_failures: Dict[str, str] = {}
        self._loaded_plugins: set[str] = set()

    def register(self, validator_type: str, cls: Type[ValidatorBase]) -> None:
        """Register a validator class for a validator_type string."""
        self._registry[validator_type] = cls

    def register_compound(self, validator_types: set, cls: Type[ValidatorBase]) -> None:
        """Register a class that handles multiple validator_types.

        The class is registered once under cls.registered_type(),
        and each secondary type is mapped to that primary key.
        """
        primary = cls.registered_type()
        self._registry[primary] = cls
        for t in validator_types:
            if t != primary:
                self._compound[t] = primary

    def lookup(self, validator_type: str) -> Optional[Type[ValidatorBase]]:
        """Look up a validator class by type. Returns None if unknown."""
        if validator_type in self._registry:
            return self._registry[validator_type]
        if validator_type in self._compound:
            primary = self._compound[validator_type]
            return self._registry.get(primary)
        return None

    def list_types(self) -> List[str]:
        """Return all registered validator types."""
        return sorted(set(list(self._registry.keys()) + list(self._compound.keys())))

    def snapshot(self) -> tuple[Dict[str, Type[ValidatorBase]], Dict[str, str]]:
        """Return a copy of the registry state for isolated test cleanup."""
        return self._registry.copy(), self._compound.copy()

    def restore(
        self, snapshot: tuple[Dict[str, Type[ValidatorBase]], Dict[str, str]]
    ) -> None:
        """Restore registry state captured by :meth:`snapshot`."""
        registry, compound = snapshot
        self._registry = registry.copy()
        self._compound = compound.copy()

    def discover_plugins(self) -> None:
        """Load installed ``snodo.validators`` entry points.

        Entry point values must resolve to a :class:`ValidatorBase` subclass.
        Each entry-point name is the protocol's ``validator_type``. Failures
        are retained for readiness/reporting callers and never abort startup.
        """
        from importlib.metadata import entry_points

        self._plugin_load_failures.clear()
        self._loaded_plugins.clear()
        try:
            plugins = entry_points(group="snodo.validators")
        except Exception as exc:
            message = f"{type(exc).__name__}: {exc}"
            self._plugin_load_failures["*"] = message
            _logger.warning("Could not discover snodo.validators entry points: %s", message)
            return

        for plugin in plugins:
            try:
                validator_cls = plugin.load()
                if not isinstance(validator_cls, type) or not issubclass(validator_cls, ValidatorBase):
                    raise TypeError("entry point must load a ValidatorBase subclass")
                self.register(plugin.name, validator_cls)
                self._loaded_plugins.add(plugin.name)
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                self._plugin_load_failures[plugin.name] = message
                _logger.warning("Could not load validator plugin '%s': %s", plugin.name, message)

    def plugin_load_failures(self) -> Dict[str, str]:
        """Return a copy of plugin load failures for readiness reporting."""
        return self._plugin_load_failures.copy()

    def plugin_status(self) -> Dict[str, Dict[str, str]]:
        """Return installed and failed validator entry points."""
        result = {name: {"status": "installed", "error": ""} for name in self._loaded_plugins}
        result.update({name: {"status": "failed", "error": error}
                       for name, error in self._plugin_load_failures.items()})
        return result


# Module-level default registry — populated on import by each validator module
_default_registry = ValidatorRegistry()

# Import built-ins only after the default registry exists. Their modules
# self-register and import this registry, so doing this from package
# ``__init__`` would expose a partially initialized module to plugin imports.
import snodo.validators.llm_validator  # noqa: E402, F401
import snodo.validators.quality  # noqa: E402, F401
import snodo.validators.protocol_adherence  # noqa: E402, F401
import snodo.validators.acceptance  # noqa: E402, F401

_default_registry.discover_plugins()


def list_validator_types() -> List[str]:
    """Return the registered validator type names from the default registry."""
    return _default_registry.list_types()
