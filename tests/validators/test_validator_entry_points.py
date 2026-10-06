"""Discovery contract for third-party validator entry points."""

from snodo.compiler.models import Validator
from snodo.validators.context import ValidatorBase
from snodo.validators.registry import ValidatorRegistry, _default_registry


class EntryPointValidator(ValidatorBase):
    def __init__(self, validator_spec):
        self.validator_spec = validator_spec
        self.validator_id = validator_spec.validator_id

    @classmethod
    def registered_type(cls):
        return "entry_point_test"

    def evaluate(self, context):
        raise NotImplementedError


class FakeEntryPoint:
    name = "entry_point_test"

    def load(self):
        return EntryPointValidator


class BrokenEntryPoint:
    name = "broken_entry_point_test"

    def load(self):
        raise ImportError("plugin dependency missing")


def test_entry_point_validator_is_registered_for_protocol_type(monkeypatch):
    from importlib import metadata

    monkeypatch.setattr(metadata, "entry_points", lambda **kwargs: [FakeEntryPoint()])
    registry = ValidatorRegistry()

    registry.discover_plugins()

    declared = Validator(
        validator_id="security",
        validator_type="entry_point_test",
        evaluation_phase="pre_execute",
        criteria=["check security"],
    )
    assert registry.lookup(declared.validator_type) is EntryPointValidator


def test_broken_entry_point_is_reported_without_crashing(monkeypatch):
    from importlib import metadata

    monkeypatch.setattr(metadata, "entry_points", lambda **kwargs: [BrokenEntryPoint()])
    registry = ValidatorRegistry()

    registry.discover_plugins()

    assert registry.lookup("broken_entry_point_test") is None
    assert registry.plugin_load_failures() == {
        "broken_entry_point_test": "ImportError: plugin dependency missing"
    }


def test_builtin_validators_remain_registered():
    from snodo.validators.quality import QualityValidator

    assert _default_registry.lookup("quality") is QualityValidator
