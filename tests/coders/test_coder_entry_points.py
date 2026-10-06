"""Installed coder adapters are discovered from setuptools entry points."""

from unittest.mock import patch

from snodo.coders import (
    CODER_REGISTRY,
    MockAdapter,
    coder_plugin_load_failures,
    discover_coder_plugins,
    get_coder,
    resolve_coder_name,
)


class FakeEntryPoint:
    def __init__(self, name, value=None, error=None):
        self.name = name
        self.value = value or "example:Adapter"
        self._error = error

    def load(self):
        if self._error:
            raise self._error
        return self.value


def test_entry_point_coder_can_be_selected_and_created():
    class ThirdPartyAdapter(MockAdapter):
        pass

    plugin = FakeEntryPoint("third-party", ThirdPartyAdapter)
    with patch("snodo.coders.entry_points", return_value=[plugin]):
        discover_coder_plugins()

    try:
        assert resolve_coder_name(model="third-party/custom-model") == "third-party"
        assert isinstance(get_coder("third-party"), ThirdPartyAdapter)
    finally:
        CODER_REGISTRY.pop("third-party", None)


def test_broken_entry_point_is_reported_without_crashing():
    plugin = FakeEntryPoint("broken-coder", error=ImportError("missing dependency"))
    with patch("snodo.coders.entry_points", return_value=[plugin]):
        discover_coder_plugins()

    assert "broken-coder" not in CODER_REGISTRY
    assert coder_plugin_load_failures() == {
        "broken-coder": "ImportError: missing dependency"
    }


def test_builtin_coders_keep_their_adapters_and_prefix_priority():
    builtin = CODER_REGISTRY["opencode-cli"]
    replacement = FakeEntryPoint("opencode-cli", MockAdapter)
    with patch("snodo.coders.entry_points", return_value=[replacement]):
        discover_coder_plugins()

    assert CODER_REGISTRY["opencode-cli"] is builtin
    assert resolve_coder_name(model="opencode-cli/custom-model") == "opencode-cli"
    assert "reserved by a built-in coder" in coder_plugin_load_failures()["opencode-cli"]
