"""Tests for PredicateRegistry.

FILE: tests/predicates/test_registry.py (Task 7.8)
"""

import pytest
from snodo.predicates.base import Predicate, PredicateResult
from snodo.predicates.registry import PredicateRegistry


class _StubPass(Predicate):
    def evaluate(self, context, **params):
        return PredicateResult(passed=True, justification="stub")


class _StubFail(Predicate):
    def evaluate(self, context, **params):
        return PredicateResult(passed=False, justification="stub fail")


def test_register_and_lookup():
    reg = PredicateRegistry()
    reg.register("stub", _StubPass())
    assert "stub" in reg


def test_lookup_returns_predicate():
    reg = PredicateRegistry()
    pred = _StubPass()
    reg.register("stub", pred)
    assert reg.lookup("stub") is pred


def test_lookup_unknown_raises():
    reg = PredicateRegistry()
    with pytest.raises(KeyError):
        reg.lookup("nonexistent")


def test_list_names():
    reg = PredicateRegistry()
    reg.register("a", _StubPass())
    reg.register("b", _StubFail())
    assert set(reg.list_names()) == {"a", "b"}


def test_default_registry_is_populated():
    import snodo.predicates.scope  # noqa: F401
    import snodo.predicates.tests  # noqa: F401
    import snodo.predicates.secrets  # noqa: F401
    from snodo.predicates.registry import _default_registry

    names = set(_default_registry.list_names())
    assert "files_in_scope" in names
    assert "tests_exist_for_modified" in names
    assert "no_secrets_in_diff" in names
