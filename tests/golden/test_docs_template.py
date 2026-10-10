"""Focused behavior checks for the technical-writer protocol template."""


def test_docs_template_is_discoverable_and_defaults_to_change_request():
    from snodo.protocols import list_templates, template_protocol

    assert "docs" in list_templates()
    protocol = template_protocol("docs")
    assert protocol.get_mode("producer") is not None
    assert protocol.execution.delivery == "change_request"


def test_docs_template_has_role_criteria_and_required_validators():
    from snodo.protocols import template_protocol

    protocol = template_protocol("docs")
    docs_quality = protocol.get_validator("docs_quality")
    assert docs_quality is not None
    criteria = " ".join(docs_quality.criteria).lower()
    for criterion in ("accurate", "code", "task-oriented", "runnable", "dead links"):
        assert criterion in criteria

    producer = protocol.get_mode("producer")
    assert producer is not None
    assert {"acceptance", "protocol"} <= set(producer.validators)
