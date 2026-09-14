"""Proposing validator criteria from decision records.

FILE: tests/survey/test_criteria.py

The proposal pass is deliberately deterministic: a rule's sentence is one that
sits under a record's Decision heading, and a record that states only a
consequence, a rejected alternative, or background is not stating a rule. The
tests hold both halves of that boundary and the attribution invariant — every
proposal carries the record it came from, resolved against the repository.
"""

from __future__ import annotations

from pathlib import Path

from snodo.survey.criteria import (
    UnknownValidatorError,
    append_criteria,
    propose_criteria,
    select_validator,
)

RULE_RECORD = """\
# ADR 001 — Derive org_id server-side

## Status

Accepted

## Context

The org id used to be taken from the request body, which let a caller name any
tenant.

## Decision

The org_id is derived server-side from the API key lookup, never accepted from
the request.

- Every D1 query carries an org filter.

## Consequences

Some queries are slower.

## Alternatives considered

Trust the client-provided org id: rejected.
"""

NON_RULE_RECORD = """\
# ADR 002 — Name the project

## Status

Accepted

## Context

We needed a name.

## Consequences

The name is long to type.

## Alternatives considered

A shorter name: rejected.
"""

EMPTY_DECISION_RECORD = """\
# ADR 003 — Record the shape

## Decision

```yaml
modules:
  - module_id: "api"
```

[See the module spec](spec.md)
"""


def _write_records(root: Path, records: dict) -> Path:
    decisions = root / "docs" / "decisions"
    decisions.mkdir(parents=True)
    for name, text in records.items():
        (decisions / name).write_text(text)
    return decisions


class TestWhatIsProposable:
    def test_a_rule_record_is_proposed_with_its_citation(self, tmp_path):
        _write_records(tmp_path, {"001-rule.md": RULE_RECORD})

        proposals = propose_criteria(tmp_path)

        criteria = [p.criterion for p in proposals]
        assert any("org_id is derived server-side" in c for c in criteria)
        assert any("Every D1 query carries an org filter" in c for c in criteria)
        for proposal in proposals:
            assert proposal.record_path == "docs/decisions/001-rule.md"
            assert (tmp_path / proposal.record_path).is_file()
            assert proposal.record_section.casefold().startswith("decision")

    def test_a_record_without_a_decision_states_no_rule(self, tmp_path):
        _write_records(tmp_path, {"002-naming.md": NON_RULE_RECORD})

        assert propose_criteria(tmp_path) == []

    def test_context_consequences_and_alternatives_are_left_on_the_table(self, tmp_path):
        decisions = _write_records(
            tmp_path,
            {"001-rule.md": RULE_RECORD, "002-naming.md": NON_RULE_RECORD},
        )
        # The non-rule record's sections mention a consequence and a rejected
        # alternative; neither is proposed even alongside a rule record.
        proposals = propose_criteria(tmp_path, decision_paths=["docs/decisions"])

        joined = "\n".join(p.criterion for p in proposals)
        assert "org filter" in joined
        assert "long to type" not in joined
        assert "rejected" not in joined
        assert {p.record_path for p in proposals} == {"docs/decisions/001-rule.md"}
        assert decisions.is_dir()

    def test_a_decision_of_only_examples_proposes_nothing(self, tmp_path):
        _write_records(tmp_path, {"003-shape.md": EMPTY_DECISION_RECORD})

        assert propose_criteria(tmp_path) == []

    def test_a_record_with_no_records_proposes_nothing(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x = 1\n")

        assert propose_criteria(tmp_path) == []


class TestWhatAcceptanceWrites:
    def test_append_criteria_is_pure_and_appends_to_the_named_validator(self):
        protocol = {
            "validators": [
                {"validator_id": "architecture", "criteria": ["keep it simple"]},
            ]
        }

        updated = append_criteria(protocol, "architecture", ["every query is scoped"])

        assert updated["validators"][0]["criteria"] == [
            "keep it simple",
            "every query is scoped",
        ]
        # The argument is untouched: acceptance is what writes, not the builder.
        assert protocol["validators"][0]["criteria"] == ["keep it simple"]

    def test_append_criteria_does_not_add_a_criterion_twice(self):
        protocol = {
            "validators": [
                {"validator_id": "architecture", "criteria": ["every query is scoped"]},
            ]
        }

        updated = append_criteria(protocol, "architecture", ["every query is scoped"])

        assert updated["validators"][0]["criteria"] == ["every query is scoped"]

    def test_append_criteria_refuses_an_unknown_validator(self):
        protocol = {"validators": [{"validator_id": "architecture"}]}

        try:
            append_criteria(protocol, "nope", ["x"])
        except UnknownValidatorError as exc:
            assert "nope" in str(exc)
        else:
            raise AssertionError("expected UnknownValidatorError")

    def test_select_validator_prefers_architecture_then_requests(self):
        protocol = {
            "validators": [
                {"validator_id": "security", "validator_type": "security"},
                {"validator_id": "arch", "validator_type": "architecture"},
            ]
        }

        assert select_validator(protocol) == "arch"
        assert select_validator(protocol, "security") == "security"

    def test_select_validator_falls_back_to_the_first_declared(self):
        protocol = {
            "validators": [
                {"validator_id": "security", "validator_type": "security"},
                {"validator_id": "quality", "validator_type": "quality"},
            ]
        }

        assert select_validator(protocol) == "security"

    def test_select_validator_refuses_a_protocol_with_no_validators(self):
        try:
            select_validator({"validators": []})
        except UnknownValidatorError as exc:
            assert "no validators" in str(exc)
        else:
            raise AssertionError("expected UnknownValidatorError")

    def test_append_criteria_creates_the_criteria_list_when_absent(self):
        protocol = {"validators": [{"validator_id": "architecture"}]}

        updated = append_criteria(protocol, "architecture", ["a rule"])

        assert updated["validators"][0]["criteria"] == ["a rule"]

    def test_an_explicit_decision_path_that_does_not_exist_proposes_nothing(self, tmp_path):
        assert propose_criteria(tmp_path, decision_paths=["docs/nowhere"]) == []

