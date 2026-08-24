"""Well-formedness tests for shipped protocol templates.

FILE: tests/golden/test_template_wellformedness.py (Task 7.13)
"""

import yaml
from pathlib import Path

import pytest

from snodo.compiler.models import Protocol
from snodo.compiler.verifier import verify_protocol


import snodo.protocols
TEMPLATES_DIR = Path(snodo.protocols.__file__).parent / "templates"


def _load(name: str) -> Protocol:
    data = yaml.safe_load((TEMPLATES_DIR / f"{name}.yml").read_text())
    return Protocol(**data)


def test_registry_contains_every_yml_in_templates_dir():
    """Every .yml in the templates directory is registered and selectable.

    Globbing the directory means a future template cannot be silently
    unregistered — adding the file is sufficient.
    """
    from snodo.protocols import PROTOCOL_TEMPLATES, list_templates, template_protocol

    on_disk = {p.stem for p in TEMPLATES_DIR.glob("*.yml")}
    assert on_disk, "no templates found in templates directory"

    registered = set(PROTOCOL_TEMPLATES.keys())
    assert on_disk == registered, (
        f"templates on disk not registered: {on_disk - registered}"
    )

    # Every registered template parses and passes WF1-WF5.
    for name in list_templates():
        proto = template_protocol(name)
        result = verify_protocol(proto)
        assert result.passed, f"{name}.yml WF violations: {result.errors}"


def test_broken_template_reported_with_file_and_condition(tmp_path, monkeypatch):
    """A template that fails WF1-WF5 is reported as broken (file + condition),
    not as missing."""
    import snodo.protocols as protocols

    broken = tmp_path / "broken.yml"
    # Two modes sharing the exclusive tool `merge` violates WF1.
    broken.write_text(
        "protocol_id: broken\n"
        "name: Broken\n"
        "modes:\n"
        "  - mode_id: a\n"
        "    tools: [merge]\n"
        "  - mode_id: b\n"
        "    tools: [merge]\n"
        "validators:\n"
        "  - validator_id: v1\n"
        "    validator_type: security\n"
        "initial_mode: a\n"
    )

    monkeypatch.setattr(protocols, "_TEMPLATES_DIR", tmp_path)
    with pytest.raises(RuntimeError) as exc:
        protocols._discover_templates()
    msg = str(exc.value)
    assert "broken.yml" in msg
    assert "WF1" in msg or "merge" in msg


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


def test_team_wf1_exclusive_tools():
    p = _load("team")
    producer = p.get_mode("producer")
    reviewer = p.get_mode("reviewer")
    assert producer is not None and reviewer is not None
    for tool in p.exclusive_tools:
        holders = [m.mode_id for m in p.modes if tool in m.tools]
        assert len(holders) <= 1, f"exclusive tool '{tool}' held by {holders}"


def test_2plus_n_wf1_exclusive_tools():
    p = _load("2+n")
    producer = p.get_mode("producer")
    reviewer = p.get_mode("reviewer")
    assert producer is not None and reviewer is not None
    for tool in p.exclusive_tools:
        holders = [m.mode_id for m in p.modes if tool in m.tools]
        assert len(holders) <= 1, f"exclusive tool '{tool}' held by {holders}"
