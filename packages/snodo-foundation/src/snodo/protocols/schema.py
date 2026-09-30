"""Build the published JSON Schema for Snodo protocol editors."""

from snodo.compiler.models import Protocol
from snodo.version import __version__


def build_protocol_schema_publication() -> dict:
    """Return the versioned protocol schema enriched with live runtime choices."""
    schema = Protocol.model_json_schema()
    from snodo.protocols.capabilities import MODE_TOOL_MAP
    from snodo.coders import CODER_REGISTRY
    import snodo.validators  # noqa: F401 — load validator registrations
    from snodo.validators.registry import list_validator_types
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
        list_validator_types(),
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
    return {
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


def _add_choices(node: dict | None, choices, nullable: bool = False) -> None:
    if not isinstance(node, dict):
        return
    values = sorted(choices.keys() if isinstance(choices, dict) else choices)
    target = node
    if nullable:
        target = next((item for item in node.get("anyOf", []) if item.get("type") == "string"), node)
    target["enum"] = values
    target["description"] = (target.get("description", "").rstrip() + " Valid choices are the values in this enum.").strip()
