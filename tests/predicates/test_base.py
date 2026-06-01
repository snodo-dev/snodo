"""Tests for predicate base types.

FILE: tests/predicates/test_base.py (Task 7.8)
"""

from snodo.predicates.base import PredicateContext, PredicateResult


def test_predicate_result_defaults():
    r = PredicateResult(passed=True, justification="ok")
    assert r.passed is True
    assert r.justification == "ok"
    assert r.evidence == {}


def test_predicate_result_with_evidence():
    r = PredicateResult(
        passed=False,
        justification="bad",
        evidence={"files": ["a.py", "b.py"]},
    )
    assert r.passed is False
    assert r.evidence == {"files": ["a.py", "b.py"]}


def test_predicate_context_fields():
    ctx = PredicateContext(
        task=None,
        mode="producer",
        artifacts=["src/a.py"],
        phase="post_validate",
    )
    assert ctx.mode == "producer"
    assert ctx.phase == "post_validate"
    assert ctx.artifacts == ["src/a.py"]
    assert ctx.workspace_mcp is None
    assert ctx.git_mcp is None
