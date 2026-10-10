"""Focused contract tests for the research analyst protocol template."""

import snodo.protocols


def test_research_template_is_discovered_and_loads():
    assert "research" in snodo.protocols.list_templates()
    protocol = snodo.protocols.template_protocol("research")
    assert protocol.protocol_id == "research"


def test_research_template_defaults_to_change_request_delivery():
    protocol = snodo.protocols.template_protocol("research")
    assert protocol.delivery_for("producer") == "change_request"


def test_research_template_has_role_criteria_and_required_validators():
    protocol = snodo.protocols.template_protocol("research")
    validator_ids = {validator.validator_id for validator in protocol.validators}
    assert {"research_quality", "acceptance", "protocol_adherence"} <= validator_ids

    research_quality = protocol.get_validator("research_quality")
    assert research_quality is not None
    criteria = " ".join(research_quality.criteria).lower()
    assert "claim" in criteria and "source" in criteria
    assert "fact" in criteria and "inference" in criteria
    assert "counter-evidence" in criteria
    assert "recommendation" in criteria and "evidence" in criteria
