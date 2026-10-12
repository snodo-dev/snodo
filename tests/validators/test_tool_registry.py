"""Plugin discovery and contract tests for validator tools."""

import logging
from pathlib import Path

import pytest
from types import SimpleNamespace

from snodo.validators.tool_registry import ValidatorTool, ValidatorToolContext, ValidatorToolRegistry


def _tool(name="ask_external_model"):
    return ValidatorTool(
        definition={
            "type": "function",
            "function": {"name": name, "description": "Ask a question", "parameters": {"type": "object"}},
        },
        handler=lambda arguments, context: f"{arguments['question']}:{context.validator_id}",
    )


def test_discovers_tool_instance_from_stub_entry_point(monkeypatch):
    tool = _tool()
    plugin = SimpleNamespace(name="decision", load=lambda: tool)
    monkeypatch.setattr("importlib.metadata.entry_points", lambda *, group: [plugin])

    registry = ValidatorToolRegistry()
    registry.discover_plugins()

    registered = registry.lookup("ask_external_model")
    assert registered is tool
    assert registered.definition == tool.definition
    context = ValidatorToolContext(workspace_root=Path("/workspace"), spec_text="spec", validator_id="v1")
    assert registered.handler({"question": "safe?"}, context) == "safe?:v1"
    assert registry.plugin_status() == {"decision": {"status": "installed", "error": ""}}


def test_plugin_load_failure_is_reported_and_skipped(monkeypatch, caplog):
    def broken_load():
        raise ImportError("missing dependency")

    plugin = SimpleNamespace(name="broken", load=broken_load)
    monkeypatch.setattr("importlib.metadata.entry_points", lambda *, group: [plugin])

    registry = ValidatorToolRegistry()
    with caplog.at_level(logging.WARNING):
        registry.discover_plugins()

    assert registry.list_tools() == []
    assert "ImportError: missing dependency" in registry.plugin_load_failures()["broken"]
    assert "broken" in caplog.text


def test_builtin_tool_names_are_refused():
    with pytest.raises(ValueError, match="read_file.*reserved by a built-in"):
        ValidatorToolRegistry().register(_tool("read_file"))
