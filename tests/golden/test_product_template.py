"""Product protocol template contract tests."""

from snodo.protocols import list_templates, template_protocol


def test_product_template_is_discoverable_and_uses_change_request_delivery():
    assert "product" in list_templates()
    protocol = template_protocol("product")
    producer = protocol.get_mode("producer")

    assert producer is not None
    assert protocol.delivery_for(producer.mode_id) == "change_request"


def test_product_template_has_role_criteria_and_required_validators():
    protocol = template_protocol("product")
    validator_ids = {validator.validator_id for validator in protocol.validators}
    assert {"product-quality", "acceptance", "protocol-adherence"} <= validator_ids

    product_quality = protocol.get_validator("product-quality")
    assert product_quality is not None
    criteria = " ".join(product_quality.criteria).lower()
    for requirement in (
        "problem",
        "evidence",
        "measurable outcomes",
        "scope and non-goals",
        "invest",
        "testable acceptance criteria",
        "technical solution",
    ):
        assert requirement in criteria
