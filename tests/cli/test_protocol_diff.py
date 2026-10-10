"""Protocol template drift CLI coverage."""

import json

import yaml
from typer.testing import CliRunner

from snodo.cli.commands.protocol_cmd import app
from snodo.protocols import template_protocol


def _invoke(tmp_path, protocol, *args):
    path = tmp_path / "protocol.yml"
    path.write_text(yaml.safe_dump(protocol.model_dump(mode="json")), encoding="utf-8")
    return CliRunner().invoke(app, ["diff", str(path), *args])


def test_no_drift_returns_zero(tmp_path):
    result = _invoke(tmp_path, template_protocol("team"))
    assert result.exit_code == 0
    assert "No drift" in result.stdout


def test_drift_in_each_group_returns_one_and_splits_project_local(tmp_path):
    original = template_protocol("team")
    data = original.model_dump(mode="python")
    data["metadata"] = {**data.get("metadata", {}), "template_name": "team"}
    data["validators"].append({"validator_id": "project_check", "validator_type": "quality"})
    data["modes"][0]["tools"] = ["read_file"]
    data["protected_paths"] = ["README.md"]
    result = _invoke(tmp_path, type(original)(**data), "--json")
    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["has_drift"] is True
    assert output["template"] == "team"
    assert output["drift"]["validators_only_in_project"]
    assert output["drift"]["changed_modes"]
    assert "protected_paths" in output["drift"]["settings"]
    assert output["drift"]["project_local"] == {}


def test_project_local_test_command_is_reported_separately(tmp_path):
    protocol = template_protocol("team")
    data = protocol.model_dump(mode="python")
    for validator in data["validators"]:
        tooling = validator.get("tooling") or {}
        if "test_command" in tooling:
            tooling["test_command"] = "pytest tests/unit"
            validator["tooling"] = tooling
            break
    else:
        data["validators"][0]["tooling"] = {"test_command": "pytest tests/unit"}
    protocol = type(protocol)(**data)
    result = _invoke(tmp_path, protocol, "--json")
    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["drift"]["project_local"]


def test_bespoke_protocol_has_clear_message_and_zero_exit(tmp_path):
    protocol = template_protocol("team").model_copy(update={"protocol_id": "custom"})
    result = _invoke(tmp_path, protocol)
    assert result.exit_code == 0
    assert "bespoke protocol" in result.stdout
    assert "snodo init --force" in result.stdout


def test_named_template_override_and_json_shape(tmp_path):
    protocol = template_protocol("team")
    result = _invoke(tmp_path, protocol, "--template", "solo", "--json")
    assert result.exit_code == 1
    output = json.loads(result.stdout)
    assert output["template"] == "solo"
    assert output["bespoke"] is False
    assert set(output["drift"]) == {
        "validators_only_in_template", "validators_only_in_project", "changed_validators",
        "modes_added", "modes_removed", "changed_modes", "settings", "project_local",
    }


def test_unknown_template_returns_usage_failure(tmp_path):
    result = _invoke(tmp_path, template_protocol("team"), "--template", "missing")
    assert result.exit_code == 2
    assert "Unknown template" in result.stdout
