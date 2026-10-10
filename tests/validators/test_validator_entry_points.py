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


def test_plugin_is_discovered_when_registry_is_imported_first(tmp_path):
    """A fresh ready invocation reports discovered and broken plugins."""
    import os
    import subprocess
    import sys

    script = r'''
import importlib.metadata
import io
import json
from contextlib import redirect_stdout
from snodo.validators.context import ValidatorBase

class PluginValidator(ValidatorBase):
    @classmethod
    def registered_type(cls):
        return "startup_plugin"
    def evaluate(self, context):
        raise NotImplementedError

class EntryPoint:
    name = "startup_plugin"
    def load(self):
        return PluginValidator

class BrokenEntryPoint:
    name = "broken_startup_plugin"
    def load(self):
        raise ImportError("plugin dependency missing")

import sys
from types import SimpleNamespace
importlib.metadata.entry_points = lambda **kwargs: [EntryPoint(), BrokenEntryPoint()]
from snodo.cli.commands.ready_cmd import ready_command
output = io.StringIO()
with redirect_stdout(output):
    code = ready_command(SimpleNamespace(mode=None, protocol=".snodo/protocol.yml", json=True))
assert code == 0
data = json.loads(output.getvalue())
assert data["extensions"]["snodo.validators"]["startup_plugin"]["status"] == "installed"
broken = data["extensions"]["snodo.validators"]["broken_startup_plugin"]
assert broken["status"] == "failed"
assert "ImportError: plugin dependency missing" == broken["error"]
'''
    # Minimal project input for the ready command; all CLI-side effects stay in
    # this temporary project, never in the repository worktree.
    (tmp_path / ".snodo").mkdir()
    (tmp_path / ".snodo" / "protocol.yml").write_text(
        "protocol_id: test\nname: Test\nversion: '1.0.0'\ninitial_mode: plan\n"
        "modes:\n  - mode_id: plan\n    name: Plan\n    validators: [check]\n"
        "validators:\n  - validator_id: check\n    validator_type: startup_plugin\n    criteria: [check]\n"
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(sys.path)
    subprocess.run([sys.executable, "-c", script], check=True, env=environment, cwd=tmp_path)
