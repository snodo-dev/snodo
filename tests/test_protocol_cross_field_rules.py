"""Keep published protocol cross-field rules aligned with enforcement."""

from copy import deepcopy

from pydantic import ValidationError

from snodo.compiler.models import Protocol
from snodo.compiler.verifier import ProtocolVerifier
from snodo.mcp.schema import build_protocol_schema_publication


def _protocol(**overrides):
    data = {
        "protocol_id": "cross-field-contract",
        "name": "Cross-field contract",
        "modes": [{"mode_id": "work", "name": "Work"}],
        "validators": [{"validator_id": "judge", "validator_type": "llm"}],
        "initial_mode": "work",
    }
    data.update(deepcopy(overrides))
    return data


def _verify(data):
    try:
        protocol = Protocol.model_validate(data)
    except ValidationError:
        return False, "protocol model rejected the document"
    result = ProtocolVerifier(protocol).verify()
    return result.passed, "; ".join(result.errors)


def _cases():
    """Return violating and satisfying documents keyed by published rule ID."""
    delivery_conflict = _protocol(
        execution={"delivery": "local_merge", "auto_merge": True}
    )
    mode_delivery_conflict = _protocol(
        modes=[{
            "mode_id": "work", "name": "Work",
            "delivery": "local_merge", "auto_merge": True,
        }]
    )
    exclusive_conflict = _protocol(
        modes=[
            {"mode_id": "work", "name": "Work", "tools": ["approve"]},
            {"mode_id": "review", "name": "Review", "tools": ["approve"]},
        ]
    )
    duplicate_modules = _protocol(
        modules=[
            {"module_id": "core", "paths": ["src"]},
            {"module_id": "core", "paths": ["tests"]},
        ]
    )
    dangling_validator = _protocol(
        modes=[{"mode_id": "work", "name": "Work", "validators": ["missing"]}]
    )
    no_pre_execute = _protocol(
        modes=[{"mode_id": "work", "name": "Work", "tools": ["dispatch"]}]
    )
    unknown_initial_mode = _protocol(initial_mode="missing")
    duplicate_modes = _protocol(
        modes=[
            {"mode_id": "work", "name": "Work"},
            {"mode_id": "work", "name": "Also work"},
        ]
    )
    duplicate_validators = _protocol(
        validators=[
            {"validator_id": "judge", "validator_type": "llm"},
            {"validator_id": "judge", "validator_type": "llm"},
        ]
    )
    return {
        "delivery-auto-merge-exclusive": (
            [delivery_conflict, mode_delivery_conflict],
            _protocol(execution={"delivery": "local_merge"}),
        ),
        "exclusive-tools-per-mode": (
            exclusive_conflict,
            _protocol(
                modes=[
                    {"mode_id": "work", "name": "Work", "tools": ["approve"]},
                    {"mode_id": "review", "name": "Review"},
                ]
            ),
        ),
        "module-ids-unique": (
            duplicate_modules,
            _protocol(modules=[{"module_id": "core", "paths": ["src"]}]),
        ),
        "validator-references-declared": (
            [dangling_validator, no_pre_execute],
            _protocol(
                modes=[{
                    "mode_id": "work",
                    "name": "Work",
                    "tools": ["dispatch"],
                    "validators": ["judge"],
                }]
            ),
        ),
        "initial-mode-exists": (
            unknown_initial_mode,
            _protocol(initial_mode="work"),
        ),
        "mode-validator-identifiers-unique": (
            [duplicate_modes, duplicate_validators],
            _protocol(),
        ),
    }


def test_every_published_cross_field_rule_has_enforcement_contract():
    published_rules = build_protocol_schema_publication()["x-snodo-cross-field-rules"]
    cases = _cases()
    published_ids = {rule["id"] for rule in published_rules}

    for rule in published_rules:
        rule_id = rule["id"]
        assert rule_id in cases, (
            f"Published cross-field rule {rule_id!r} has no violating/valid test case"
        )

    assert set(cases) == published_ids, (
        "Cross-field contract cases must correspond exactly to published rules; "
        f"unpublished cases: {sorted(set(cases) - published_ids)}"
    )

    for rule in published_rules:
        rule_id = rule["id"]
        invalid_documents, valid = cases[rule_id]
        if not isinstance(invalid_documents, list):
            invalid_documents = [invalid_documents]
        for invalid in invalid_documents:
            passed, _errors = _verify(invalid)
            assert not passed, f"Rule {rule_id!r} unexpectedly accepted its violation"

        passed, errors = _verify(valid)
        assert passed, f"Rule {rule_id!r} rejected its satisfying protocol: {errors}"
