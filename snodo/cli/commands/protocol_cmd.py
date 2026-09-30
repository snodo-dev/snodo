"""Read-only protocol schema publication commands."""

import json

import typer

COMMAND_NAME = "protocol"
app = typer.Typer(invoke_without_command=True, help="Inspect and work with protocol definitions")


@app.callback()
def _protocol_callback(ctx: typer.Context):
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@app.command(name="schema")
def protocol_schema(
    json_output: bool = typer.Option(False, "--json", help="Emit the protocol schema as JSON"),
):
    """Print the generated JSON Schema for protocol.yml."""
    return protocol_schema_command(json_output=json_output)


def protocol_schema_command(json_output: bool = True) -> int:
    """Print the versioned Protocol model schema, enriched with live choices."""
    from snodo.compiler.models import Protocol
    from snodo.version import __version__

    schema = Protocol.model_json_schema()
    from snodo.mcp.tools import MODE_TOOL_MAP
    from snodo.coders import CODER_REGISTRY
    import snodo.validators  # noqa: F401 — load validator registrations
    from snodo.validators.registry import _default_registry
    from snodo.providers.registry import list_providers
    from snodo.protocols import PROTOCOL_TEMPLATES

    definitions = schema.get("$defs", {})
    _add_choices(
        definitions.get("Mode", {}).get("properties", {}).get("tools", {}).get("items"),
        MODE_TOOL_MAP,
    )
    _add_choices(
        definitions.get("Mode", {}).get("properties", {}).get("coder"),
        CODER_REGISTRY,
        nullable=True,
    )
    _add_choices(
        definitions.get("Validator", {}).get("properties", {}).get("validator_type"),
        _default_registry.list_types(),
    )
    schema["properties"]["metadata"]["x-snodo-open"] = True
    definitions.get("Validator", {}).get("properties", {}).get("tooling", {})["x-snodo-open"] = True
    definitions.get("Module", {}).get("properties", {}).get("tooling", {})["x-snodo-open"] = True
    definitions.get("Mode", {}).get("properties", {}).get("coder_config", {})["x-snodo-open"] = True
    definitions.get("Constraint", {}).get("properties", {}).get("params", {})["x-snodo-open"] = True
    schema["x-snodo-choices"] = {
        "providers": sorted(list_providers()),
        "templates": sorted(PROTOCOL_TEMPLATES),
        "description": "Runtime choices for editor controls; provider/template choices are not protocol fields.",
    }
    publication = {
        "schema_id": "snodo.protocol.v1",
        "snodo_version": __version__,
        "schema": schema,
        "x-snodo-cross-field-rules": [
            {"id": "delivery-auto-merge-exclusive", "description": "Protocol execution and each mode may not set delivery and auto_merge together; use delivery."},
            {"id": "exclusive-tools-per-mode", "description": "Each exclusive tool may appear in at most one mode (WF1)."},
            {"id": "module-ids-unique", "description": "Module identifiers must be unique."},
            {"id": "validator-references-declared", "description": "Validators referenced by modes and modules must be declared; dispatch modes require a pre_execute validator (WF3)."},
            {"id": "initial-mode-exists", "description": "initial_mode must name a declared mode (WF3)."},
            {"id": "mode-validator-identifiers-unique", "description": "Mode identifiers and validator identifiers must each be unique."},
        ],
    }
    print(json.dumps(publication, indent=2, sort_keys=True))
    return 0


def _add_choices(node: dict | None, choices, nullable: bool = False):
    if not isinstance(node, dict):
        return
    values = sorted(choices.keys() if isinstance(choices, dict) else choices)
    target = node
    if nullable:
        target = next((item for item in node.get("anyOf", []) if item.get("type") == "string"), node)
    target["enum"] = values
    target["description"] = (target.get("description", "").rstrip() + " Valid choices are the values in this enum.").strip()
