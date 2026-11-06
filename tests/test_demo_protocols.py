"""Tests for the demo protocol builders."""

import importlib.util
from pathlib import Path
import shutil

import pytest
import yaml

ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location(
    "demo_protocols", ROOT / "demo" / "protocols.py"
)
demo_protocols = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(demo_protocols)


def test_bare_differs_from_governed_only_in_validators_and_recovery_depth():
    governed = yaml.safe_load(demo_protocols.governed_protocol_text())
    bare = yaml.safe_load(demo_protocols.bare_protocol_text())
    governed_mode = next(
        mode
        for mode in governed["modes"]
        if mode["mode_id"] == governed["initial_mode"]
    )
    bare_mode = next(
        mode for mode in bare["modes"] if mode["mode_id"] == bare["initial_mode"]
    )
    assert bare_mode["validators"] == ["triage"]
    governed_mode["validators"] = bare_mode["validators"]
    assert bare["execution"]["max_recovery_depth"] == 0
    governed["execution"]["max_recovery_depth"] = 0
    assert bare == governed


def test_both_protocols_parse_as_yaml():
    assert isinstance(yaml.safe_load(demo_protocols.governed_protocol_text()), dict)
    assert isinstance(yaml.safe_load(demo_protocols.bare_protocol_text()), dict)


def test_governed_equals_shipped_template():
    template = (
        ROOT
        / "packages"
        / "snodo-foundation"
        / "src"
        / "snodo"
        / "protocols"
        / "templates"
        / "bugfix-surgeon.yml"
    )
    assert demo_protocols.governed_protocol_text() == template.read_text(
        encoding="utf-8"
    )


def test_protocols_validate_when_snodo_available(tmp_path):
    if shutil.which("snodo") is None:
        pytest.skip("snodo command is not available")
    for protocol_text in (
        demo_protocols.governed_protocol_text(),
        demo_protocols.bare_protocol_text(),
    ):
        path = demo_protocols.write_protocol(protocol_text, tmp_path / "protocol.yml")
        valid, findings = demo_protocols.validate_protocol(path)
        assert valid, findings
