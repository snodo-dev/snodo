"""The concrete anchors a spec carries, kept across a redefinition (Fixes #320).

A reauthored spec replaces the original for every later validator and the coder.
These tests pin the extraction (what counts as evidence), the subtraction (what
a rewrite dropped) and the repair (putting it back verbatim), including that a
spec with no evidence is left untouched.
"""

from snodo.engine.evidence import (
    evidence_missing,
    extract_evidence,
    preserve_evidence,
)


class TestExtractEvidence:
    def test_path_line_citation_is_one_anchor(self):
        assert extract_evidence("Broken at `src/booking.py:42`.") == [
            "src/booking.py:42"
        ]

    def test_word_line_citation_is_one_anchor(self):
        assert extract_evidence("Broken at src/booking.py line 42.") == [
            "src/booking.py line 42"
        ]

    def test_named_test_survives(self):
        assert "test_booking_flow" in extract_evidence("See test_booking_flow.")

    def test_bare_path_survives(self):
        assert "pyproject.toml" in extract_evidence("Bump pyproject.toml.")

    def test_backticked_code_literal_survives(self):
        assert '<input type="url">' in extract_evidence(
            'The field is `<input type="url">`.'
        )

    def test_no_evidence_is_empty(self):
        assert extract_evidence("Implement feature X") == []

    def test_version_and_domain_are_not_evidence(self):
        assert extract_evidence("Bump to 1.0.0 for cal.com/you") == []

    def test_longer_anchor_subsumes_shorter(self):
        assert extract_evidence("`src/a.py:42`") == ["src/a.py:42"]


class TestPreserveEvidence:
    def test_missing_anchor_is_appended_verbatim(self):
        out = preserve_evidence("Fix it.", ["src/a.py:42"])
        assert "Fix it." in out
        assert "src/a.py:42" in out

    def test_nothing_missing_is_unchanged(self):
        assert preserve_evidence("Fix it.", []) == "Fix it."

    def test_empty_authored_still_carries_evidence(self):
        assert "src/a.py:42" in preserve_evidence("", ["src/a.py:42"])

    def test_missing_helper_reports_only_dropped_anchors(self):
        assert evidence_missing(
            "kept src/a.py:42", ["src/a.py:42", "test_x"]
        ) == ["test_x"]
