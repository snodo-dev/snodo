"""Validator registry — validator_type → ValidatorBase class mapping.

FILE: snodo/validators/registry.py (Task 7.20)

Mirrors the PredicateRegistry pattern from 7.8.  Module-level default
singleton with self-registration by each validator module on import.
"""

from typing import Dict, List, Optional, Type

from snodo.validators.context import ValidatorBase


class ValidatorRegistry:
    """Maps validator_type strings to ValidatorBase subclasses."""

    def __init__(self) -> None:
        self._registry: Dict[str, Type[ValidatorBase]] = {}
        self._compound: Dict[str, str] = {}  # type → primary key

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


# Module-level default registry — populated on import by each validator module
_default_registry = ValidatorRegistry()
