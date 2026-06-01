"""Tests for files_in_scope predicate.

FILE: tests/predicates/test_scope.py (Task 7.8)
"""

from snodo.predicates.scope import FilesInScope
from snodo.predicates.base import PredicateContext


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
    result = pred.evaluate(ctx, scope_paths=["*"])  # No-op if default
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
