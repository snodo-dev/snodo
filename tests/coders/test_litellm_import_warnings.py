"""Tests for LiteLLM import-time warning suppression (Fixes #135).

Importing `snodo.coders.litellm` must not emit LiteLLM `register_model`
warnings (they fire for every Cloudflare model in the catalog on every
invocation, including `snodo --version`). The suppression is scoped to the
`register_model` call only — a warning logged AFTER import must still
propagate. That second test is the one that matters.
"""

import logging


def test_import_emits_no_litellm_warnings(caplog):
    """Importing the module emits no LiteLLM warning records."""
    import importlib

    with caplog.at_level(logging.WARNING, logger="LiteLLM"):
        importlib.import_module("snodo.coders.litellm")

    records = [r for r in caplog.records if r.name == "LiteLLM"]
    assert records == [], (
        "importing snodo.coders.litellm emitted LiteLLM warnings: "
        + "\n".join(f"{r.levelname}: {r.getMessage()}" for r in records)
    )


def test_warning_after_import_still_propagates(caplog):
    """A LiteLLM warning logged AFTER import still propagates — the
    suppression was narrowed to the register_model call, not disabled."""
    import snodo.coders.litellm  # noqa: F401 — import side effect is the point

    with caplog.at_level(logging.WARNING, logger="LiteLLM"):
        logging.getLogger("LiteLLM").warning("real routing warning")

    records = [r for r in caplog.records if r.name == "LiteLLM"]
    assert any("real routing warning" in r.getMessage() for r in records), (
        "a LiteLLM warning logged after import was swallowed — the "
        "suppression was not scoped to the register_model call"
    )
