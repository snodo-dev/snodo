"""Well-formedness tests for shipped protocol templates.

FILE: tests/golden/test_template_wellformedness.py (Task 7.13)
"""

from pathlib import Path

import pytest
import snodo.protocols
import yaml
from snodo.compiler.models import Protocol
from snodo.compiler.verifier import verify_protocol

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
    assert len(p.validators) == 5
    assert p.initial_mode == "producer"
    ids = {v.validator_id for v in p.validators}
    assert ids == {"security", "architecture", "quality", "meta-spec", "acceptance"}


def test_team_structure():
    p = _load("team")
    assert p.protocol_id == "default"
    assert len(p.modes) == 3
    mode_ids = {m.mode_id for m in p.modes}
    assert mode_ids == {"producer", "reviewer", "planner"}
    assert len(p.validators) == 11
    ids = {v.validator_id for v in p.validators}
    assert "protocol_adherence" in ids
    assert "acceptance" in ids


def test_2plus_n_structure():
    p = _load("2+n")
    assert p.protocol_id == "2+n"
    assert len(p.modes) == 2
    mode_ids = {m.mode_id for m in p.modes}
    assert mode_ids == {"producer", "reviewer"}
    assert len(p.validators) == 7
    ids = {v.validator_id for v in p.validators}
    assert "protocol_adherence" in ids
    assert "acceptance" in ids


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


def test_repository_content_validators_grant_read_tools():
    """security/architecture (and conventions in 2+n) grant read_file+list_files.

    These validators carry criteria phrased as facts about the repository, so
    they must be able to open it.  meta-spec deliberately gets none — it judges
    the spec, and the spec is all it should see.
    """
    read_tools = {"read_file", "list_files"}

    for name in ("solo", "team", "2+n"):
        p = _load(name)
        for vid in ("security", "architecture"):
            v = p.get_validator(vid)
            assert v is not None, f"{name}: missing {vid}"
            assert set(v.tools) == read_tools, f"{name}.{vid} tools={v.tools}"

    # 2+n also grants conventions (naming/file-organization criteria).
    p = _load("2+n")
    assert set(p.get_validator("conventions").tools) == read_tools

    # meta-spec judges the spec only — no tools, in every template that ships it.
    for name in ("solo", "team", "2+n"):
        p = _load(name)
        meta = p.get_validator("meta-spec")
        assert meta is not None
        assert meta.tools == [], f"{name}.meta-spec should have no tools"


def test_2plus_n_wf1_exclusive_tools():
    p = _load("2+n")
    producer = p.get_mode("producer")
    reviewer = p.get_mode("reviewer")
    assert producer is not None and reviewer is not None
    for tool in p.exclusive_tools:
        holders = [m.mode_id for m in p.modes if tool in m.tools]
        assert len(holders) <= 1, f"exclusive tool '{tool}' held by {holders}"


SINGLE_OPERATOR_TEMPLATES = ["solo", "greenfield", "bugfix-surgeon", "feature-warden", "intent"]
PRODUCER_REVIEWER_TEMPLATES = ["team", "2+n"]


@pytest.mark.parametrize("name", SINGLE_OPERATOR_TEMPLATES)
def test_single_operator_templates_auto_merge(name):
    p = _load(name)
    result = verify_protocol(p)
    assert result.passed, f"{name}.yml WF violations: {result.errors}"
    for mode in p.modes:
        assert p.auto_merge_enabled(mode.mode_id) is True, (
            f"{name}.yml mode '{mode.mode_id}' must auto-merge (single-operator protocol)"
        )


@pytest.mark.parametrize("name", PRODUCER_REVIEWER_TEMPLATES)
def test_producer_reviewer_templates_do_not_auto_merge(name):
    """An unreviewed auto-merge would defeat the producer/reviewer separation,
    so those templates keep merging a deliberate operator action."""
    p = _load(name)
    for mode in p.modes:
        assert p.auto_merge_enabled(mode.mode_id) is False, (
            f"{name}.yml mode '{mode.mode_id}' must NOT auto-merge"
        )


def test_shipped_quality_templates_ship_the_noop_default():
    """Every shipped template with a quality validator ships the exact no-op
    default test command.

    A fresh project initialised from a template in a directory with no marker
    file must never halt on an unresolvable test command. The template's
    default must match the validator's constant exactly: the validator
    recognises that literal and records outcome "no_tests" (no tests executed)
    rather than a false pass. Auto-detection and `--test-command` replace this
    default; the default is only what remains when neither applies.
    """
    from snodo.protocols import list_templates, template_protocol
    from snodo.validators.quality import NOOP_TEST_COMMAND

    shipped = []
    for name in list_templates():
        p = template_protocol(name)
        q = p.get_validator("quality")
        if q is None:
            continue
        tooling = q.tooling or {}
        tc = tooling.get("test_command")
        assert tc == NOOP_TEST_COMMAND, (
            f"{name}.yml quality validator must ship the exact no-op default "
            f"test command (got {tc!r}) so an unconfigured project can run and "
            "the validator can recognise the no-op it executed"
        )
        shipped.append(name)

    assert {"solo", "team", "2+n", "greenfield"} <= set(shipped), (
        f"expected solo/team/2+n/greenfield to ship the quality validator, got {shipped}"
    )
