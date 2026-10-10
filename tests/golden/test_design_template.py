"""Focused behavior checks for the designer-work protocol template."""

def test_design_template_is_discoverable_and_defaults_to_change_request():
    from snodo.protocols import list_templates, template_protocol

    assert "design" in list_templates()
    protocol = template_protocol("design")
    producer = protocol.get_mode("producer")
    assert producer is not None
    assert protocol.execution.delivery == "change_request"


def test_design_template_has_role_criteria_and_required_validators():
    from snodo.protocols import template_protocol

    protocol = template_protocol("design")
    design_quality = protocol.get_validator("design_quality")
    assert design_quality is not None
    criteria = " ".join(design_quality.criteria).lower()
    for role_criterion in ("user", "empty", "loading", "error", "success", "wcag", "contrast", "keyboard", "design system", "plain", "kind"):
        assert role_criterion in criteria

    producer = protocol.get_mode("producer")
    assert producer is not None
    assert {"acceptance", "protocol"} <= set(producer.validators)
