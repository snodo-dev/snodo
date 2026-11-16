from pathlib import Path

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "snodo" / "web" / "static" / "protocol-validators.js"


def test_open_validator_editor_uses_mockup_structure_and_safe_dom() -> None:
    source = SOURCE.read_text()
    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    for class_name in ("edit", "fields", "seg", "on", "crit", "mod", "textarea", "x", "btn"):
        assert f'"{class_name}"' in source or f'`{class_name}' in source
    for label in ("Type", "Phase", "Severity cap", "Tools (read-only)", "Criteria", "+ Add criterion"):
        assert label in source
    assert '"pre_execute", "post_execute"' in source
    assert '"none", ...SEVERITIES' in source
    assert 'tools.value.split(",")' in source
    assert '"×"' in source
    assert '"Remove from" : "Add to"' in source


def test_editor_updates_shared_working_copy_without_recreating_active_input() -> None:
    source = SOURCE.read_text()
    assert "validator.criteria[index] = area.value" in source
    assert "validator.tools = tools.value" in source
    assert "validator[control.dataset.f]" in source
    assert "mode.validators.splice(index, 1)" in source
    assert "mode.validators.push(validator.id)" in source
    assert "existing.contains(focused)" in source
    assert "replacement.replaceWith(focused)" in source
    assert "criterion !== (original.criteria || [])[index]" in source
