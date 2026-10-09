"""Offline integration coverage for the standalone example validator package."""

import importlib.util
from importlib.metadata import EntryPoint
from pathlib import Path
import sys
from types import SimpleNamespace

from snodo.validators.registry import ValidatorRegistry


ROOT = Path(__file__).parents[1]
PLUGIN_DIR = ROOT / "examples" / "snodo-hello-validator"


def test_example_validator_entry_point_ready_and_verdicts(monkeypatch):
    """Discovery reports the plugin and its class gives the expected verdicts."""
    spec = importlib.util.spec_from_file_location(
        "snodo_hello_validator", PLUGIN_DIR / "snodo_hello_validator.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setitem(sys.modules, "snodo_hello_validator", module)
    plugin = EntryPoint(
        name="hello_acceptance",
        value="snodo_hello_validator:HelloAcceptanceValidator",
        group="snodo.validators",
    )
    monkeypatch.setattr("importlib.metadata.entry_points", lambda *, group: [plugin])
    registry = ValidatorRegistry()
    registry.discover_plugins()
    assert registry.lookup("hello_acceptance") is module.HelloAcceptanceValidator
    assert registry.plugin_status() == {"hello_acceptance": {"status": "installed", "error": ""}}

    # Exercise the same readiness aggregation helper used by `snodo ready`;
    # it reads plugin status without requiring project state or invoking CLI.
    from snodo.cli.commands.ready_cmd import _extension_plugin_status

    monkeypatch.setattr("snodo.coders.coder_plugin_status", lambda: {})
    monkeypatch.setattr("snodo.providers.registry.provider_plugin_status", lambda: {})
    monkeypatch.setattr("snodo.predicates.registry._default_registry", SimpleNamespace(plugin_status=lambda: {}))
    monkeypatch.setattr("snodo.validators.registry._default_registry", registry)
    extensions = _extension_plugin_status()
    assert extensions["snodo.validators"]["hello_acceptance"]["status"] == "installed"

    validator = module.HelloAcceptanceValidator(SimpleNamespace(validator_id="example"))
    with_section = SimpleNamespace(task=SimpleNamespace(spec="# Task\n\n## ACCEPTANCE\n- Done"))
    without_section = SimpleNamespace(task=SimpleNamespace(spec="# Task\nDo the work."))
    assert validator.evaluate(with_section).severity == "pass"
    assert validator.evaluate(without_section).severity == "warn"


def test_example_is_not_in_main_distribution_configuration():
    """The main setuptools package finder remains scoped to Snodo packages."""
    text = (ROOT / "pyproject.toml").read_text()
    assert 'include = ["snodo*"]' in text
    assert "examples" not in text
