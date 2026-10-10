"""Pure comparisons between loaded project and template protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from snodo.compiler.models import Protocol, Validator


@dataclass(frozen=True)
class ValueChange:
    """A value differing between the project protocol and its template."""

    template: Any
    project: Any


@dataclass(frozen=True)
class ValidatorChange:
    validator_id: str
    changes: dict[str, ValueChange]


@dataclass(frozen=True)
class ModeChange:
    mode_id: str
    changes: dict[str, ValueChange]


@dataclass(frozen=True)
class ProtocolDiff:
    """Structured protocol drift, with template values listed before project values."""

    validators_only_in_template: tuple[str, ...] = ()
    validators_only_in_project: tuple[str, ...] = ()
    changed_validators: tuple[ValidatorChange, ...] = ()
    modes_added: tuple[str, ...] = ()
    modes_removed: tuple[str, ...] = ()
    changed_modes: tuple[ModeChange, ...] = ()
    settings: dict[str, ValueChange] = field(default_factory=dict)
    project_local: dict[str, ValueChange] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not any((
            self.validators_only_in_template, self.validators_only_in_project,
            self.changed_validators, self.modes_added, self.modes_removed,
            self.changed_modes, self.settings, self.project_local,
        ))


def _normalized(value: Any, *, unordered: bool = False) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="python")
    if isinstance(value, dict):
        return tuple(sorted((key, _normalized(item, unordered=key in {"tools", "validators"})) for key, item in value.items()))
    if isinstance(value, (list, tuple, set, frozenset)):
        items = [_normalized(item) for item in value]
        return tuple(sorted(items, key=repr))
    return value


def _change(template: Any, project: Any, *, unordered: bool = False) -> ValueChange | None:
    if _normalized(template, unordered=unordered) == _normalized(project, unordered=unordered):
        return None
    return ValueChange(template=template, project=project)


def _validator_changes(template: Validator, project: Validator) -> tuple[dict[str, ValueChange], dict[str, ValueChange]]:
    changes: dict[str, ValueChange] = {}
    local: dict[str, ValueChange] = {}
    fields = ("validator_type", "evaluation_phase", "criteria", "tools", "severity_cap", "scope")
    for name in fields:
        difference = _change(getattr(template, name), getattr(project, name), unordered=name == "tools")
        if difference:
            changes[name] = difference
    template_tooling = dict(template.tooling)
    project_tooling = dict(project.tooling)
    template_command = template_tooling.pop("test_command", None)
    project_command = project_tooling.pop("test_command", None)
    if (difference := _change(template_command, project_command)):
        local["test_command"] = difference
    if (difference := _change(template_tooling, project_tooling)):
        changes["tooling"] = difference
    return changes, local


def diff_protocols(project: Protocol, template: Protocol) -> ProtocolDiff:
    """Compare loaded protocol models, ignoring list order for allowlists.

    ``project`` is the customized protocol and ``template`` is its source. The
    result always expresses values as ``template`` then ``project``.
    """
    template_validators = {item.validator_id: item for item in template.validators}
    project_validators = {item.validator_id: item for item in project.validators}
    changed_validators: list[ValidatorChange] = []
    project_local: dict[str, ValueChange] = {}
    for validator_id in sorted(template_validators.keys() & project_validators.keys()):
        changes, local = _validator_changes(template_validators[validator_id], project_validators[validator_id])
        if changes:
            changed_validators.append(ValidatorChange(validator_id, changes))
        if local:
            project_local[f"validators.{validator_id}.test_command"] = local["test_command"]

    template_modes = {item.mode_id: item for item in template.modes}
    project_modes = {item.mode_id: item for item in project.modes}
    changed_modes: list[ModeChange] = []
    for mode_id in sorted(template_modes.keys() & project_modes.keys()):
        before, after = template_modes[mode_id], project_modes[mode_id]
        changes = {}
        for name in ("tools", "validators", "transitions", "delivery"):
            difference = _change(getattr(before, name), getattr(after, name), unordered=name in {"tools", "validators"})
            if difference:
                changes[name] = difference
        if changes:
            changed_modes.append(ModeChange(mode_id, changes))

    settings: dict[str, ValueChange] = {}
    for name in ("initial_mode", "disagreement_policy", "protected_paths", "write_allowed_prefixes", "global_constraints"):
        difference = _change(getattr(template, name), getattr(project, name), unordered=name in {"protected_paths", "write_allowed_prefixes"})
        if difference:
            settings[name] = difference
    template_execution = template.execution.model_dump(mode="python")
    project_execution = project.execution.model_dump(mode="python")
    template_delivery = template_execution.pop("delivery")
    project_delivery = project_execution.pop("delivery")
    if difference := _change(template_execution, project_execution):
        settings["execution"] = difference
    if difference := _change(template_delivery, project_delivery):
        settings["delivery"] = difference

    return ProtocolDiff(
        validators_only_in_template=tuple(sorted(template_validators.keys() - project_validators.keys())),
        validators_only_in_project=tuple(sorted(project_validators.keys() - template_validators.keys())),
        changed_validators=tuple(changed_validators),
        modes_added=tuple(sorted(project_modes.keys() - template_modes.keys())),
        modes_removed=tuple(sorted(template_modes.keys() - project_modes.keys())),
        changed_modes=tuple(changed_modes),
        settings=settings,
        project_local=project_local,
    )
