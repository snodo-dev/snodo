"""Protocol validation CLI coverage."""

import json

import yaml
from typer.testing import CliRunner

from snodo.cli.commands.protocol_cmd import app


VALID = {
    "protocol_id": "example",
    "name": "Example",
    "modes": [{"mode_id": "build", "name": "Build", "tools": ["edit"], "validators": ["quality"]}],
    "validators": [{"validator_id": "quality", "validator_type": "quality"}],
    "initial_mode": "build",
}


def _run(tmp_path, payload):
    path = tmp_path / "protocol.yml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    return CliRunner().invoke(app, ["validate", str(path), "--json"])


def test_valid_protocol_passes(tmp_path):
    result = _run(tmp_path, VALID)
    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_unknown_capability_fails(tmp_path):
    payload = {**VALID, "modes": [{**VALID["modes"][0], "tools": ["planning"]}]}
    result = _run(tmp_path, payload)
    assert result.exit_code != 0
    assert any("planning" in item["message"] for item in json.loads(result.stdout))


def test_missing_capability_name_fails_schema_validation(tmp_path):
    payload = {**VALID, "modes": [{**VALID["modes"][0], "tools": ["dispatch"], "validators": []}]}
    result = _run(tmp_path, payload)
    assert result.exit_code != 0
    assert any("pre_execute validator" in item["message"] for item in json.loads(result.stdout))


def test_unknown_field_fails(tmp_path):
    result = _run(tmp_path, {**VALID, "unexpected": True})
    assert result.exit_code != 0
    assert any("unexpected" in item["message"] for item in json.loads(result.stdout))


def test_bad_choice_value_fails(tmp_path):
    payload = {**VALID, "disagreement_policy": "sometimes"}
    result = _run(tmp_path, payload)
    assert result.exit_code != 0
    assert any("sometimes" in item["message"] for item in json.loads(result.stdout))


def test_open_dictionary_accepts_unknown_keys(tmp_path):
    payload = {**VALID, "metadata": {"custom": "value"}}
    result = _run(tmp_path, payload)
    assert result.exit_code == 0, result.stdout
