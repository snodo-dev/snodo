"""Tests for no_secrets_in_diff predicate.

FILE: tests/predicates/test_secrets.py (Task 7.8)
"""

from unittest.mock import Mock

from snodo.predicates.secrets import NoSecretsInDiff
from snodo.predicates.base import PredicateContext


def test_pre_execute_passes_trivially():
    pred = NoSecretsInDiff()
    ctx = PredicateContext(
        task=None, mode="producer", artifacts=[],
        phase="governance",
    )
    result = pred.evaluate(ctx)
    assert result.passed is True
    assert "pre-execute" in result.justification.lower()


def test_no_git_mcp_passes():
    pred = NoSecretsInDiff()
    ctx = PredicateContext(
        task=None, mode="producer", artifacts=[],
        phase="post_validate",
    )
    result = pred.evaluate(ctx)
    assert result.passed is True


def test_empty_diff_passes():
    pred = NoSecretsInDiff()
    git = Mock()
    git.read_diff.return_value = ""
    ctx = PredicateContext(
        task=None, mode="producer", artifacts=[],
        git_mcp=git, phase="post_validate",
    )
    result = pred.evaluate(ctx)
    assert result.passed is True


def test_clean_diff_passes():
    pred = NoSecretsInDiff()
    git = Mock()
    git.read_diff.return_value = (
        "+def hello():  # normal code\n"
        "+    return 'world'\n"
    )
    ctx = PredicateContext(
        task=None, mode="producer", artifacts=[],
        git_mcp=git, phase="post_validate",
    )
    result = pred.evaluate(ctx)
    assert result.passed is True


def test_aws_key_in_diff_fails():
    pred = NoSecretsInDiff()
    git = Mock()
    git.read_diff.return_value = (
        "+def hello():\n"
        "+    key = 'AKIA1234567890ABCDEF'  # AWS key\n"
    )
    ctx = PredicateContext(
        task=None, mode="producer", artifacts=[],
        git_mcp=git, phase="post_validate",
    )
    result = pred.evaluate(ctx)
    assert result.passed is False
    assert "secrets" in result.justification.lower()
    assert result.evidence["findings"]


def test_removed_line_only_passes():
    """Secret in removed line should not trigger (only additions checked)."""
    pred = NoSecretsInDiff()
    git = Mock()
    git.read_diff.return_value = (
        "-key = 'AKIA1234567890ABCDEF'  # removed\n"
        "+key = None  # replaced\n"
    )
    ctx = PredicateContext(
        task=None, mode="producer", artifacts=[],
        git_mcp=git, phase="post_validate",
    )
    result = pred.evaluate(ctx)
    assert result.passed is True


def test_diff_header_ignored():
    """+++ header lines should be ignored."""
    pred = NoSecretsInDiff()
    git = Mock()
    git.read_diff.return_value = (
        "+++ b/src/secrets.py\n"
        "+def normal_code(): pass\n"
    )
    ctx = PredicateContext(
        task=None, mode="producer", artifacts=[],
        git_mcp=git, phase="post_validate",
    )
    result = pred.evaluate(ctx)
    assert result.passed is True
