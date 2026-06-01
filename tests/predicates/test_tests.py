"""Tests for tests_exist_for_modified predicate.

FILE: tests/predicates/test_tests.py (Task 7.8)
"""

from unittest.mock import Mock

from snodo.predicates.tests import TestsExistForModified
from snodo.predicates.base import PredicateContext


def test_pre_execute_passes_trivially():
    pred = TestsExistForModified()
    ctx = PredicateContext(
        task=None, mode="producer", artifacts=[],
        phase="governance",
    )
    result = pred.evaluate(ctx)
    assert result.passed is True
    assert "no artifacts" in result.justification.lower()


def test_no_workspace_passes():
    pred = TestsExistForModified()
    ctx = PredicateContext(
        task=None, mode="producer", artifacts=["src/main.py"],
        phase="post_validate",
    )
    result = pred.evaluate(ctx)
    assert result.passed is True
    assert "no workspace" in result.justification.lower()


def test_all_tests_exist_passes():
    pred = TestsExistForModified()
    workspace = Mock()
    workspace.file_exists.return_value = True
    ctx = PredicateContext(
        task=None, mode="producer",
        artifacts=["src/main.py", "src/utils.py"],
        workspace_mcp=workspace,
        phase="post_validate",
    )
    result = pred.evaluate(ctx)
    assert result.passed is True


def test_missing_test_fails():
    pred = TestsExistForModified()
    workspace = Mock()
    workspace.file_exists.return_value = False
    ctx = PredicateContext(
        task=None, mode="producer",
        artifacts=["src/main.py"],
        workspace_mcp=workspace,
        phase="post_validate",
    )
    result = pred.evaluate(ctx)
    assert result.passed is False
    assert "tests/test_main.py" in result.justification


def test_only_test_files_passes():
    pred = TestsExistForModified()
    workspace = Mock()
    ctx = PredicateContext(
        task=None, mode="producer",
        artifacts=["tests/test_main.py", "tests/test_utils.py"],
        workspace_mcp=workspace,
        phase="post_validate",
    )
    result = pred.evaluate(ctx)
    assert result.passed is True
    # workspace.file_exists should never be called for test files


def test_custom_patterns():
    pred = TestsExistForModified()
    workspace = Mock()
    workspace.file_exists.return_value = False
    ctx = PredicateContext(
        task=None, mode="producer",
        artifacts=["app/controllers/user_controller.rb"],
        workspace_mcp=workspace,
        phase="post_validate",
    )
    result = pred.evaluate(
        ctx,
        test_dir_pattern="spec/",
        test_name_pattern="{stem}_spec.rb",
    )
    assert result.passed is False
    assert "spec/user_controller_spec.rb" in result.justification


def test_git_commit_artifact_skipped():
    """Non-code artifacts (like 'git_commit') should be skipped."""
    pred = TestsExistForModified()
    workspace = Mock()
    ctx = PredicateContext(
        task=None, mode="producer",
        artifacts=["git_commit"],
        workspace_mcp=workspace,
        phase="post_validate",
    )
    result = pred.evaluate(ctx)
    assert result.passed is True
