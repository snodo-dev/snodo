"""Protocol schema command contract tests."""

import json

import yaml
from typer.testing import CliRunner

from snodo.cli.commands.protocol_cmd import app


def test_protocol_schema_publishes_runtime_choices_and_rules():
    result = CliRunner().invoke(app, ["schema", "--json"])
    assert result.exit_code == 0, result.output
    published = json.loads(result.stdout)
    schema = published["schema"]
    defs = schema["$defs"]
    assert published["schema_id"] == "snodo.protocol.v1"
    assert published["snodo_version"]
    assert {"modes", "validators", "execution", "initial_mode"} <= schema["properties"].keys()
    execution = defs["ExecutionConfig"]["properties"]
    assert execution["delivery"]["anyOf"][0]["enum"] == ["local_merge", "push_branch", "change_request"]
    assert execution["max_recovery_depth"]["maximum"] == 20
    assert execution["max_recovery_depth"]["minimum"] == 0
    capabilities = defs["Mode"]["properties"]["tools"]["items"]["enum"]
    assert {"plan", "queue", "edit", "dispatch"} <= set(capabilities)
    assert not {"resolve", "planning"} & set(capabilities)
    coders = defs["Mode"]["properties"]["coder"]["anyOf"][0]["enum"]
    assert {"opencode-cli", "codex-cli"} <= set(coders)
    assert schema["properties"]["metadata"]["x-snodo-open"] is True
    assert published["x-snodo-cross-field-rules"]


def test_shipped_templates_validate_against_published_schema():
    import jsonschema

    from snodo.protocols import PROTOCOL_TEMPLATES

    published = json.loads(CliRunner().invoke(app, ["schema", "--json"]).stdout)
    schema = published["schema"]
    for name, raw in PROTOCOL_TEMPLATES.items():
        jsonschema.validate(yaml.safe_load(raw), schema, format_checker=jsonschema.FormatChecker())
