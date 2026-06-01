"""Journey 7: Invalid protocol rejection.

FILE: tests/e2e/test_protocol_validation.py (Task 7.13)

Tests that well-formedness checks reject invalid protocols.
"""

from pathlib import Path

import pytest


def _write_protocol(tmp_path, yaml_content: str) -> Path:
    """Write a protocol file to the .snodo directory in the working dir."""
    snodo_dir = tmp_path / ".snodo"
    snodo_dir.mkdir(parents=True, exist_ok=True)
    proto_file = snodo_dir / "protocol.yml"
    proto_file.write_text(yaml_content)
    return proto_file


@pytest.mark.e2e
def test_mode_references_missing_validator(snodo_cli):
    """Mode references a validator_id not declared in validators list."""
    invalid_yml = """protocol_id: "bad"
name: "Bad Protocol"
version: "1.0.0"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit"]
    validators: ["nonexistent"]
    transitions: {}
validators:
  - validator_id: "security"
    validator_type: "security"
    evaluation_phase: "pre_execute"
disagreement_policy: "unanimous"
initial_mode: "producer"
global_constraints: []
"""
    _write_protocol(snodo_cli.home, invalid_yml)
    r = snodo_cli(["run", "task", "--mock"])
    assert r.returncode != 0


@pytest.mark.e2e
def test_mode_tools_overlap_rejected(snodo_cli):
    """Two modes sharing a tool violates WF1."""
    invalid_yml = """protocol_id: "bad"
name: "Bad Protocol"
version: "1.0.0"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit", "review"]
    validators: ["sec"]
    transitions: {}
  - mode_id: "reviewer"
    name: "Reviewer"
    tools: ["review", "approve"]
    validators: ["sec"]
    transitions: {}
validators:
  - validator_id: "sec"
    validator_type: "security"
    evaluation_phase: "pre_execute"
disagreement_policy: "unanimous"
initial_mode: "producer"
global_constraints: []
"""
    _write_protocol(snodo_cli.home, invalid_yml)
    r = snodo_cli(["run", "task", "--mock"])
    assert r.returncode != 0


@pytest.mark.e2e
def test_missing_initial_mode_rejected(snodo_cli):
    """initial_mode must reference a valid mode."""
    invalid_yml = """protocol_id: "bad"
name: "Bad"
version: "1.0.0"
modes:
  - mode_id: "producer"
    name: "Producer"
    tools: ["edit"]
    validators: ["sec"]
    transitions: {}
validators:
  - validator_id: "sec"
    validator_type: "security"
    evaluation_phase: "pre_execute"
disagreement_policy: "unanimous"
initial_mode: "nonexistent"
global_constraints: []
"""
    _write_protocol(snodo_cli.home, invalid_yml)
    r = snodo_cli(["run", "task", "--mock"])
    assert r.returncode != 0
