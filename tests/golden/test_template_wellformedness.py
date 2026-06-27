"""Well-formedness tests for shipped protocol templates.

FILE: tests/golden/test_template_wellformedness.py (Task 7.13)
"""

import yaml
from pathlib import Path

from snodo.compiler.models import Protocol
from snodo.compiler.verifier import verify_protocol


import snodo.protocols
TEMPLATES_DIR = Path(snodo.protocols.__file__).parent / "templates"


def _load(name: str) -> Protocol:
    data = yaml.safe_load((TEMPLATES_DIR / f"{name}.yml").read_text())
    return Protocol(**data)


def test_solo_wf():
    p = _load("solo")
    result = verify_protocol(p)
    assert result.passed, f"solo.yml WF violations: {result.errors}"


def test_team_wf():
    p = _load("team")
    result = verify_protocol(p)
    assert result.passed, f"team.yml WF violations: {result.errors}"


def test_2plus_n_wf():
    p = _load("2+n")
    result = verify_protocol(p)
    assert result.passed, f"2+n.yml WF violations: {result.errors}"


def test_solo_structure():
    p = _load("solo")
    assert p.protocol_id == "solo"
    assert len(p.modes) == 1
    assert p.modes[0].mode_id == "producer"
    assert len(p.validators) == 4
    assert p.initial_mode == "producer"
    ids = {v.validator_id for v in p.validators}
    assert ids == {"security", "architecture", "quality", "meta-spec"}


def test_team_structure():
    p = _load("team")
    assert p.protocol_id == "default"
    assert len(p.modes) == 3
    mode_ids = {m.mode_id for m in p.modes}
    assert mode_ids == {"producer", "reviewer", "planner"}
    assert len(p.validators) == 10
    ids = {v.validator_id for v in p.validators}
    assert "protocol_adherence" in ids


def test_2plus_n_structure():
    p = _load("2+n")
    assert p.protocol_id == "2+n"
    assert len(p.modes) == 2
    mode_ids = {m.mode_id for m in p.modes}
    assert mode_ids == {"producer", "reviewer"}
    assert len(p.validators) == 6
    ids = {v.validator_id for v in p.validators}
    assert "protocol_adherence" in ids


def test_2plus_n_has_severity_cap():
    p = _load("2+n")
    pa = p.get_validator("protocol_adherence")
    assert pa is not None
    assert pa.severity_cap is not None
    assert pa.severity_cap.value == "warn"


def test_team_has_severity_cap():
    p = _load("team")
    pa = p.get_validator("protocol_adherence")
    assert pa is not None
    assert pa.severity_cap is not None
    assert pa.severity_cap.value == "warn"


def test_2plus_n_constraints_reference_known_predicates():
    p = _load("2+n")
    assert len(p.global_constraints) == 3
    predicate_names = {c.predicate for c in p.global_constraints}
    assert predicate_names == {"files_in_scope", "tests_exist_for_modified", "no_secrets_in_diff"}


def test_team_producer_has_protocol_adherence():
    p = _load("team")
    producer = p.get_mode("producer")
    assert producer is not None
    assert "protocol_adherence" in producer.validators


def test_2plus_n_producer_has_protocol_adherence():
    p = _load("2+n")
    producer = p.get_mode("producer")
    assert producer is not None
    assert "protocol_adherence" in producer.validators


def test_team_wf1_disjoint_tools():
    p = _load("team")
    producer = p.get_mode("producer")
    reviewer = p.get_mode("reviewer")
    assert producer is not None and reviewer is not None
    assert set(producer.tools).isdisjoint(set(reviewer.tools))


def test_2plus_n_wf1_disjoint_tools():
    p = _load("2+n")
    producer = p.get_mode("producer")
    reviewer = p.get_mode("reviewer")
    assert producer is not None and reviewer is not None
    assert set(producer.tools).isdisjoint(set(reviewer.tools))
