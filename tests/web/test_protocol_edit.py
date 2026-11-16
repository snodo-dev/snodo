"""Tests for comment-preserving protocol edits."""

import yaml
import pytest

from snodo.web.protocol_edit import ProtocolEditError, apply_protocol_edits


FIXTURE = '''# header stays
modes:
  - mode_id: "producer"
    validators:
      - "one"
validators:
  - validator_id: "one"
    validator_type: "quality"
    evaluation_phase: "pre_execute"
    # cap comment
    severity_cap: "warn"
    tools:
      - "read_file"
    criteria:
      - 'Keep: this "quote"'
      # attached comment
      - "Second criterion"

  - validator_id: "unused"
    validator_type: "architecture"
    criteria:
      - "Untouched"
'''


def test_validator_edits_keep_surrounding_bytes_and_escape_scalars():
    value = 'quotes " : slash \\ café'
    output, changes = apply_protocol_edits(FIXTURE, [{
        "validator_id": "one", "type": value, "phase": "post_execute",
        "severity_cap": None, "tools": [], "criteria": [value, "Second criterion"],
    }])
    parsed = yaml.safe_load(output)
    assert parsed["validators"][0]["validator_type"] == value
    assert parsed["validators"][0]["criteria"] == [value, "Second criterion"]
    assert "# header stays\nmodes:" in output
    assert '# attached comment' in output
    assert 'validator_id: "unused"' in output
    assert len(changes) == 5


def test_mode_empty_list_can_be_removed_and_repopulated():
    out, _ = apply_protocol_edits(FIXTURE, [{"mode_id": "producer", "remove_validator": "one"}])
    assert yaml.safe_load(out)["modes"][0]["validators"] == []
    out, _ = apply_protocol_edits(out, [{"mode_id": "producer", "add_validator": "one"}])
    assert yaml.safe_load(out)["modes"][0]["validators"] == ["one"]


def test_empty_criteria_remains_list_and_noop_is_identical():
    assert apply_protocol_edits(FIXTURE, []) == (FIXTURE, [])
    out, _ = apply_protocol_edits(FIXTURE, [{"validator_id": "one", "criteria": []}])
    assert yaml.safe_load(out)["validators"][0]["criteria"] == []


def test_unknown_ids_raise_and_windows_line_endings_survive():
    with pytest.raises(ProtocolEditError, match="unknown validator"):
        apply_protocol_edits(FIXTURE, [{"validator_id": "missing", "phase": "pre_execute"}])
    text = FIXTURE.replace("\n", "\r\n").rstrip("\r\n")
    output, _ = apply_protocol_edits(text, [{"validator_id": "one", "phase": "post_execute"}])
    assert "\r\n" in output and "\n" not in output.replace("\r\n", "")
    assert not output.endswith("\r\n")


def test_repository_protocol_criteria_edit_is_local():
    from pathlib import Path

    text = Path(".snodo/protocol.yml").read_text()
    before = yaml.safe_load(text)
    target = next(v for v in before["validators"] if v["validator_id"] == "security")
    criteria = list(target["criteria"])
    criteria.append("editor test criterion")
    output, _ = apply_protocol_edits(text, [{"validator_id": "security", "criteria": criteria}])
    assert yaml.safe_load(output)["validators"][0]["criteria"] == criteria
    assert output.replace('      - "editor test criterion"\n', "") == text

