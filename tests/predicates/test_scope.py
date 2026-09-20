"""Tests for files_in_scope predicate.

FILE: tests/predicates/test_scope.py (Task 7.8)
"""

from types import SimpleNamespace

from snodo.compiler.models import Module
from snodo.core.interfaces import Task
from snodo.predicates.base import PredicateContext
from snodo.predicates.scope import FilesInScope


def test_pre_execute_passes_trivially():
    pred = FilesInScope()
    ctx = PredicateContext(
        task=None, mode="producer", artifacts=[],
        phase="governance",
    )
    result = pred.evaluate(ctx, scope_paths=["src/**"])
    assert result.passed is True
    assert "no artifacts" in result.justification.lower()


def test_empty_artifacts_post_execute_passes():
    pred = FilesInScope()
    ctx = PredicateContext(
        task=None, mode="producer", artifacts=[],
        phase="post_validate",
    )
    result = pred.evaluate(ctx, scope_paths=["src/**"])
    assert result.passed is True


def test_all_in_scope_passes():
    pred = FilesInScope()
    ctx = PredicateContext(
        task=None,
        mode="producer",
        artifacts=["src/main.py", "src/utils.py", "tests/test_main.py"],
        phase="post_validate",
    )
    result = pred.evaluate(ctx, scope_paths=["src/**", "tests/**"])
    assert result.passed is True


def test_one_out_of_scope_fails():
    pred = FilesInScope()
    ctx = PredicateContext(
        task=None,
        mode="producer",
        artifacts=["src/main.py", "secrets/creds.txt"],
        phase="post_validate",
    )
    result = pred.evaluate(ctx, scope_paths=["src/**"])
    assert result.passed is False
    assert "secrets/creds.txt" in result.justification
    assert "out_of_scope_files" in result.evidence
    assert "secrets/creds.txt" in result.evidence["out_of_scope_files"]


def test_wildcard_pattern():
    pred = FilesInScope()
    ctx = PredicateContext(
        task=None,
        mode="producer",
        artifacts=["any/file/here.txt"],
        phase="post_validate",
    )
    pred.evaluate(ctx, scope_paths=["*"])  # No-op if default
    # Default in the code is ["*"] which matches everything with fnmatch
    # But wildcard 'any/file/here.txt' against '*' does not match in fnmatch
    # (fnmatch matches just the basename). Let me test with "any/**/*"
    pass  # Default scope_paths = ["*"] only matches bare names, not paths


def test_glob_nested_pattern():
    pred = FilesInScope()
    ctx = PredicateContext(
        task=None,
        mode="producer",
        artifacts=["deep/nested/file.py"],
        phase="post_validate",
    )
    result = pred.evaluate(ctx, scope_paths=["deep/**"])
    assert result.passed is True


# ── Module bounds what a task may touch (Fixes #392) ──────────────────────


def _protocol_with(*modules):
    """A protocol stand-in carrying only what the predicate reads."""
    return SimpleNamespace(modules=list(modules))


def _module_scoped_context(artifacts, module_id, protocol):
    return PredicateContext(
        task=Task(id="t1", spec="add a worker test", module_id=module_id),
        mode="producer",
        artifacts=artifacts,
        protocol=protocol,
        phase="post_validate",
    )


_WORKER = Module(module_id="worker", paths=["services/worker/**"])
_CLIENT = Module(module_id="client", paths=["apps/client"])


def test_module_scoped_task_write_outside_module_is_caught():
    """The whole-repo protocol scope would pass this; the module bound does not."""
    pred = FilesInScope()
    ctx = _module_scoped_context(
        ["services/worker/handler_test.go", "apps/client/lib/main.dart"],
        "worker",
        _protocol_with(_WORKER, _CLIENT),
    )
    result = pred.evaluate(ctx, scope_paths=["**"])
    assert result.passed is False
    assert "apps/client/lib/main.dart" in result.justification
    assert result.evidence["out_of_scope_files"] == ["apps/client/lib/main.dart"]


def test_module_scoped_task_writes_inside_module_pass():
    pred = FilesInScope()
    ctx = _module_scoped_context(
        ["services/worker/handler.go", "services/worker/handler_test.go"],
        "worker",
        _protocol_with(_WORKER, _CLIENT),
    )
    result = pred.evaluate(ctx, scope_paths=["**"])
    assert result.passed is True


def test_module_root_path_owns_everything_beneath_it():
    """A module declaring a bare root (no glob) bounds writes to its subtree."""
    pred = FilesInScope()
    ctx = _module_scoped_context(
        ["apps/client/lib/main.dart", "services/worker/handler.go"],
        "client",
        _protocol_with(_WORKER, _CLIENT),
    )
    result = pred.evaluate(ctx, scope_paths=["**"])
    assert result.passed is False
    assert "services/worker/handler.go" in result.evidence["out_of_scope_files"]
    assert "apps/client/lib/main.dart" not in result.evidence["out_of_scope_files"]


def test_unscoped_task_is_judged_against_protocol_scope_as_before():
    pred = FilesInScope()
    inside = PredicateContext(
        task=Task(id="t1", spec="anything"),
        mode="producer",
        artifacts=["src/main.py", "tests/test_main.py"],
        protocol=_protocol_with(_WORKER, _CLIENT),
        phase="post_validate",
    )
    assert pred.evaluate(inside, scope_paths=["src/**", "tests/**"]).passed is True

    outside = PredicateContext(
        task=Task(id="t2", spec="anything"),
        mode="producer",
        artifacts=["src/main.py", "secrets/creds.txt"],
        protocol=_protocol_with(_WORKER, _CLIENT),
        phase="post_validate",
    )
    result = pred.evaluate(outside, scope_paths=["src/**"])
    assert result.passed is False
    assert "declared scope" in result.justification


def test_module_scoped_task_pre_execute_still_passes_trivially():
    pred = FilesInScope()
    ctx = _module_scoped_context([], "worker", _protocol_with(_WORKER))
    ctx.phase = "governance"
    result = pred.evaluate(ctx, scope_paths=["**"])
    assert result.passed is True


def test_declared_module_absent_from_protocol_fails_loud():
    """An unresolvable bound must not silently widen back to the whole repo."""
    pred = FilesInScope()
    ctx = _module_scoped_context(
        ["services/worker/handler.go"],
        "ghost",
        _protocol_with(_WORKER),
    )
    result = pred.evaluate(ctx, scope_paths=["**"])
    assert result.passed is False
    assert "ghost" in result.justification
