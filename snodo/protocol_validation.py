"""Read-only validation of protocol YAML against Snodo's published schema."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from snodo.mcp.schema import build_protocol_schema_publication


def validate_protocol(path: str | Path = ".snodo/protocol.yml") -> list[dict[str, str]]:
    """Return protocol validation findings using existing pass/warn/blocker severities."""
    protocol_path = Path(path)
    try:
        data = yaml.safe_load(protocol_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return [{"path": str(protocol_path), "message": str(exc), "severity": "blocker"}]

    publication = build_protocol_schema_publication()
    schema = deepcopy(publication["schema"])
    _close_model_fields(schema)
    validator = jsonschema.validators.validator_for(schema)(schema)
    findings = [
        {
            "path": _format_path(error.absolute_path),
            "message": error.message,
            "severity": "blocker",
        }
        for error in validator.iter_errors(data)
    ]
    if isinstance(data, dict):
        findings.extend(_cross_field_findings(data))
    return sorted(findings, key=lambda finding: (finding["path"], finding["message"]))


def _format_path(parts: Any) -> str:
    result = "$"
    for part in parts:
        result += f"[{part}]" if isinstance(part, int) else f".{part}"
    return result


def _close_model_fields(schema: dict[str, Any]) -> None:
    """Make model objects closed while preserving explicitly open dictionaries."""
    for node in [schema, *schema.get("$defs", {}).values()]:
        if isinstance(node, dict):
            _close_node(node)


def _close_node(node: dict[str, Any]) -> None:
    if node.get("type") == "object" and node.get("properties") and not node.get("x-snodo-open"):
        node["additionalProperties"] = False
    for value in node.values():
        if isinstance(value, dict):
            _close_node(value)
        elif isinstance(value, list):
            for child in value:
                if isinstance(child, dict):
                    _close_node(child)


def _cross_field_findings(data: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []

    def add(path: str, message: str) -> None:
        findings.append({"path": path, "message": message, "severity": "blocker"})

    modes = data.get("modes", [])
    validators = data.get("validators", [])
    modules = data.get("modules", [])
    if not all(isinstance(items, list) for items in (modes, validators, modules)):
        return findings
    if not all(isinstance(item, dict) for items in (modes, validators, modules) for item in items):
        return findings
    mode_ids = [item.get("mode_id") for item in modes if isinstance(item, dict)]
    validator_ids = [item.get("validator_id") for item in validators if isinstance(item, dict)]
    module_ids = [item.get("module_id") for item in modules if isinstance(item, dict)]
    for key, values, label in (("modes", mode_ids, "mode_id"), ("validators", validator_ids, "validator_id")):
        if len(values) != len(set(values)):
            add(f"$.{key}", f"{label} values must be unique.")
    if len(module_ids) != len(set(module_ids)):
        add("$.modules", "Module identifiers must be unique.")
    if data.get("initial_mode") not in mode_ids:
        add("$.initial_mode", "initial_mode must name a declared mode.")

    declared = set(validator_ids)
    pre_execute = {v.get("validator_id") for v in validators if isinstance(v, dict) and v.get("evaluation_phase", "pre_execute") == "pre_execute"}
    exclusive = set(data.get("exclusive_tools", ["approve", "merge"])) | {"approve", "merge"}
    owners: dict[str, str] = {}
    for index, mode in enumerate(modes):
        if not isinstance(mode, dict):
            continue
        for validator_id in mode.get("validators", []):
            if validator_id not in declared:
                add(f"$.modes[{index}].validators", f"Validator {validator_id!r} is not declared.")
        if "dispatch" in mode.get("tools", []) and not (set(mode.get("validators", [])) & pre_execute):
            add(f"$.modes[{index}].validators", "Modes with dispatch require a pre_execute validator.")
        for tool in set(mode.get("tools", [])) & exclusive:
            if tool in owners:
                add(f"$.modes[{index}].tools", f"Exclusive tool {tool!r} also belongs to mode {owners[tool]!r}.")
            else:
                owners[tool] = str(mode.get("mode_id", index))
    for index, module in enumerate(modules):
        if isinstance(module, dict):
            for validator_id in module.get("validators", []):
                if validator_id not in declared:
                    add(f"$.modules[{index}].validators", f"Validator {validator_id!r} is not declared.")
    execution = data.get("execution") or {}
    if isinstance(execution, dict) and "delivery" in execution and "auto_merge" in execution:
        add("$.execution", "Set either delivery or auto_merge, not both.")
    for index, mode in enumerate(modes):
        if isinstance(mode, dict) and "delivery" in mode and "auto_merge" in mode:
            add(f"$.modes[{index}]", "Set either delivery or auto_merge, not both.")
    return findings
