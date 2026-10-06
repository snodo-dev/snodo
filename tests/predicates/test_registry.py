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
    import snodo.predicates.secrets  # noqa: F401
    import snodo.predicates.tests  # noqa: F401
    from snodo.predicates.registry import _default_registry

    names = set(_default_registry.list_names())
    assert "files_in_scope" in names
    assert "tests_exist_for_modified" in names
    assert "no_secrets_in_diff" in names


class _FakeEntryPoint:
    name = "entry_point_test"

    def load(self):
        return _StubPass


class _BrokenEntryPoint:
    name = "broken_entry_point_test"

    def load(self):
        raise ImportError("plugin dependency missing")


def test_entry_point_predicate_can_be_referenced_by_protocol(monkeypatch):
    from importlib import metadata
    from snodo.compiler.models import Constraint

    monkeypatch.setattr(metadata, "entry_points", lambda **kwargs: [_FakeEntryPoint()])
    registry = PredicateRegistry()
    registry.discover_plugins()

    declared = Constraint(
        constraint_id="plugin-check",
        description="Run plugin predicate",
        predicate="entry_point_test",
    )
    assert registry.lookup(declared.predicate).evaluate(None).passed


def test_broken_entry_point_is_reported_without_crashing(monkeypatch):
    from importlib import metadata

    monkeypatch.setattr(metadata, "entry_points", lambda **kwargs: [_BrokenEntryPoint()])
    registry = PredicateRegistry()
    registry.discover_plugins()

    assert "broken_entry_point_test" not in registry
    assert registry.plugin_load_failures() == {
        "broken_entry_point_test": "ImportError: plugin dependency missing"
    }


def test_discovery_preserves_builtin_predicates(monkeypatch):
    from importlib import metadata
    import snodo.predicates.scope  # noqa: F401
    import snodo.predicates.secrets  # noqa: F401
    import snodo.predicates.tests  # noqa: F401
    from snodo.predicates.registry import _default_registry

    monkeypatch.setattr(metadata, "entry_points", lambda **kwargs: [])
    _default_registry.discover_plugins()
    assert {"files_in_scope", "tests_exist_for_modified", "no_secrets_in_diff"} <= set(
        _default_registry.list_names()
    )
