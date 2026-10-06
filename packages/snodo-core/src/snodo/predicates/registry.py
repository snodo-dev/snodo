"""Predicate registry — name → Predicate mapping.

FILE: snodo/predicates/registry.py (Task 7.8)

Supports module-level default singleton and constructor injection for
test isolation (matching the existing audit_log / session_manager / 
token_issuer pattern).
"""

import logging
from typing import Dict, List

from snodo.predicates.base import Predicate

_logger = logging.getLogger(__name__)


class PredicateRegistry:
    """Maps predicate names to Predicate instances."""

    def __init__(self) -> None:
        self._predicates: Dict[str, Predicate] = {}
        self._plugin_load_failures: Dict[str, str] = {}

    def register(self, name: str, predicate: Predicate) -> None:
        """Register a predicate under the given name.

        Args:
            name: Predicate name (e.g. "files_in_scope")
            predicate: Predicate instance
        """
        self._predicates[name] = predicate

    def lookup(self, name: str) -> Predicate:
        """Look up a predicate by name.

        Args:
            name: Predicate name

        Returns:
            Predicate instance

        Raises:
            KeyError: If no predicate is registered under that name.
        """
        return self._predicates[name]

    def list_names(self) -> List[str]:
        """Return all registered predicate names."""
        return list(self._predicates.keys())

    def __contains__(self, name: str) -> bool:
        return name in self._predicates

    def discover_plugins(self) -> None:
        """Load installed ``snodo.predicates`` entry points.

        Entry points may provide a Predicate subclass or an instance. Failures
        are retained for diagnostic consumers and never prevent startup.
        """
        from importlib.metadata import entry_points

        try:
            points = entry_points(group="snodo.predicates")
        except Exception as exc:
            _logger.warning("Could not discover snodo.predicates entry points: %s", exc)
            self._plugin_load_failures["<discovery>"] = f"{type(exc).__name__}: {exc}"
            return
        for point in points:
            try:
                predicate = point.load()
                if isinstance(predicate, type) and issubclass(predicate, Predicate):
                    predicate = predicate()
                if not isinstance(predicate, Predicate):
                    raise TypeError("entry point must load a Predicate subclass or instance")
                self.register(point.name, predicate)
                self._plugin_load_failures.pop(point.name, None)
            except Exception as exc:
                self._plugin_load_failures[point.name] = f"{type(exc).__name__}: {exc}"
                _logger.warning(
                    "Could not load predicate plugin '%s': %s: %s",
                    point.name, type(exc).__name__, exc,
                )

    def plugin_load_failures(self) -> Dict[str, str]:
        """Return entry-point load failures keyed by plugin name."""
        return dict(self._plugin_load_failures)


# Module-level default registry — populated by individual predicate modules
_default_registry = PredicateRegistry()
_default_registry.discover_plugins()
