"""Predicate framework for constraint evaluation (Task 7.8).

A predicate is a named, deterministic check over execution context.
Predicates are the deterministic floor complementing LLM validators
as the bounded-non-deterministic ceiling.
"""

from snodo.predicates.base import Predicate, PredicateContext, PredicateResult
from snodo.predicates.registry import PredicateRegistry, _default_registry

__all__ = ["Predicate", "PredicateContext", "PredicateResult", "PredicateRegistry", "_default_registry"]
