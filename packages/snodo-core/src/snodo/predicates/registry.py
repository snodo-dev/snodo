"""Predicate registry — name → Predicate mapping.

FILE: snodo/predicates/registry.py (Task 7.8)

Supports module-level default singleton and constructor injection for
test isolation (matching the existing audit_log / session_manager / 
token_issuer pattern).
"""

from typing import Dict, List

from snodo.predicates.base import Predicate


class PredicateRegistry:
    """Maps predicate names to Predicate instances."""

    def __init__(self) -> None:
        self._predicates: Dict[str, Predicate] = {}

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


# Module-level default registry — populated by individual predicate modules
_default_registry = PredicateRegistry()
