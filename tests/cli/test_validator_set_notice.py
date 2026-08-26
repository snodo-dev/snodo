"""Tests for the out-of-date validator-set notice (Fixes #59).

Adding a validator to a shipped template does NOT add it to a project whose
.snodo/protocol.yml predates the change.  The project silently keeps running
the old validator set — the same failure pattern as a validator that does
nothing.  These tests assert the notice fires for a pre-ADR-028 project and
does not fire for one already on the current validator set.
"""

from snodo.cli.commands.run_cmd import _print_missing_template_validators


class TestMissingTemplateValidators:
    def test_missing_validators_returns_acceptance_for_old_solo(self):
        """A pre-ADR-028 solo protocol is missing 'acceptance'."""
        from snodo.protocols import missing_template_validators, template_protocol

        solo = template_protocol("solo")
        # Remove the acceptance validator to simulate a pre-ADR-028 project.
        from snodo.compiler.models import Protocol

        data = solo.model_dump()
        data["validators"] = [
            v for v in data["validators"] if v["validator_id"] != "acceptance"
        ]
        old_solo = Protocol(**data)

        missing = missing_template_validators(old_solo)
        assert missing == ["acceptance"]

    def test_current_validator_set_reports_nothing_missing(self):
        from snodo.protocols import missing_template_validators, template_protocol

        assert missing_template_validators(template_protocol("solo")) == []
        assert missing_template_validators(template_protocol("team")) == []
        assert missing_template_validators(template_protocol("2+n")) == []

    def test_bespoke_protocol_reports_nothing_missing(self):
        from snodo.protocols import missing_template_validators
        from snodo.compiler.models import Protocol, Mode, Validator

        bespoke = Protocol(
            protocol_id="my-custom",
            name="Custom",
            modes=[Mode(mode_id="producer", name="Producer", tools=["edit"])],
            validators=[Validator(validator_id="v1", validator_type="security")],
            initial_mode="producer",
            disagreement_policy="unanimous",
        )
        assert missing_template_validators(bespoke) == []


class TestOutOfDateNotice:
    def test_notice_printed_when_acceptance_missing(self, capsys):
        from snodo.protocols import template_protocol
        from snodo.compiler.models import Protocol

        solo = template_protocol("solo")
        data = solo.model_dump()
        data["validators"] = [
            v for v in data["validators"] if v["validator_id"] != "acceptance"
        ]
        old_solo = Protocol(**data)

        _print_missing_template_validators(old_solo)
        out = capsys.readouterr().out
        assert "out of date" in out
        assert "acceptance" in out

    def test_no_notice_for_current_validator_set(self, capsys):
        from snodo.protocols import template_protocol

        _print_missing_template_validators(template_protocol("solo"))
        assert capsys.readouterr().out == ""
