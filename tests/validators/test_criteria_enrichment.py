"""Tests for criteria index citation legibility and payload enrichment (Fixes #37).

FILE: tests/validators/test_criteria_enrichment.py
"""

from snodo.core.interfaces import ValidatorResult
from snodo.validators.runner import enrich_result_with_criteria, extract_cited_indices


def test_extract_cited_indices_single():
    """Extract single criterion index citations."""
    indices = extract_cited_indices("Violates criterion 3: hardcoded credentials", 5)
    assert indices == [3]

    indices_hash = extract_cited_indices("Failed criterion #2", 5)
    assert indices_hash == [2]


def test_extract_cited_indices_multiple():
    """Extract multiple criteria index citations."""
    indices = extract_cited_indices("Failed criteria 1 and 3: missing auth checks and hardcoded secrets", 5)
    assert indices == [1, 3]

    indices_comma = extract_cited_indices("Violates rules 1, 2, and 4", 5)
    assert indices_comma == [1, 2, 4]


def test_extract_cited_indices_out_of_bounds_ignored():
    """Indices higher than total criteria length are ignored."""
    indices = extract_cited_indices("Violates criterion 99", 3)
    assert indices == []


def test_enrich_result_with_single_cited_criterion():
    """Enrich ValidatorResult with single cited criterion text."""
    criteria = [
        "All endpoints must validate auth tokens",
        "Input parameters must be sanitized",
        "No API keys or credentials may be hardcoded in source files",
    ]
    res = ValidatorResult(
        validator_id="security",
        severity="blocker",
        justification="Violates criterion 3: hardcoded credentials in src/auth.py",
    )

    enriched = enrich_result_with_criteria(res, criteria)

    assert enriched.cited_criteria == [
        "[Criterion 3] No API keys or credentials may be hardcoded in source files"
    ]
    assert "criterion 3 ('No API keys or credentials may be hardcoded in source files')" in enriched.justification


def test_enrich_result_with_multiple_cited_criteria():
    """Enrich ValidatorResult with multiple cited criteria."""
    criteria = [
        "All endpoints must validate auth tokens",
        "Input parameters must be sanitized",
        "No API keys or credentials may be hardcoded in source files",
    ]
    res = ValidatorResult(
        validator_id="security",
        severity="blocker",
        justification="Failed criteria 1 and 2: missing auth checks and raw queries",
    )

    enriched = enrich_result_with_criteria(res, criteria)

    assert enriched.cited_criteria == [
        "[Criterion 1] All endpoints must validate auth tokens",
        "[Criterion 2] Input parameters must be sanitized",
    ]
    assert "All endpoints must validate auth tokens" in enriched.justification
    assert "Input parameters must be sanitized" in enriched.justification


def test_enrich_result_truncates_long_criterion_in_justification():
    """Long criteria inlined into justification are excerpted with an ellipsis."""
    long_criterion = "X" * 150
    criteria = ["Rule 1", long_criterion]

    res = ValidatorResult(
        validator_id="quality",
        severity="warn",
        justification="Violates criterion 2: long rule broken",
    )

    enriched = enrich_result_with_criteria(res, criteria)

    # Full text retained in cited_criteria
    assert enriched.cited_criteria == [f"[Criterion 2] {long_criterion}"]

    # Truncated 100-char excerpt in justification
    assert "criterion 2 ('" + "X" * 100 + "...')" in enriched.justification


def test_enrich_result_uncited_has_no_overhead():
    """When no criterion index is cited, cited_criteria remains None."""
    criteria = ["Rule 1", "Rule 2"]
    res = ValidatorResult(
        validator_id="testing",
        severity="pass",
        justification="All automated tests passed successfully",
    )

    enriched = enrich_result_with_criteria(res, criteria)

    assert enriched.cited_criteria is None
    assert enriched.justification == "All automated tests passed successfully"
