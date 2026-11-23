"""Behavior-level checks for recon empty-result diagnostics."""

from types import SimpleNamespace
from unittest.mock import patch


def _configure_model(monkeypatch):
    from snodo.config import ConfigManager

    monkeypatch.setattr(ConfigManager, "resolve_litellm_model", staticmethod(lambda model: model))
    monkeypatch.setattr(ConfigManager, "resolve_api_base", staticmethod(lambda model: None))
    monkeypatch.setattr(
        ConfigManager, "resolve_extra_headers", staticmethod(lambda model, task_id=None: None)
    )
    monkeypatch.setattr(ConfigManager, "get_key_for_model", lambda self, model: None)


def test_empty_length_ending_warning_reports_cause_and_output_tokens(tmp_path, monkeypatch, caplog):
    import logging

    import snodo.recon as recon_module

    _configure_model(monkeypatch)

    def empty_length_response(token_count):
        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content="", tool_calls=[]),
                finish_reason="length",
            )],
            usage=SimpleNamespace(completion_tokens=token_count),
        )

    with patch(
        "litellm.completion",
        side_effect=[empty_length_response(17), empty_length_response(23)],
    ) as completion, caplog.at_level(logging.WARNING, logger="snodo.recon"):
        result = recon_module.call_agent(
            str(tmp_path), "test/model", "query", [], "agent", max_turns=1
        )

    warning = next(
        record.getMessage()
        for record in caplog.records
        if "Recon agent returned empty result" in record.getMessage()
    )
    assert completion.call_count == 2
    assert result.result == ""
    assert "ending=truncated:length" in warning
    assert "output_tokens=40" in warning
    assert "auth" not in warning.lower()


def test_authentication_failure_is_error_not_length_warning(tmp_path, monkeypatch, caplog):
    import logging

    import snodo.recon as recon_module

    _configure_model(monkeypatch)
    authentication_error = RuntimeError("Authentication failed: invalid API key")

    with patch("litellm.completion", side_effect=authentication_error), caplog.at_level(
        logging.WARNING, logger="snodo.recon"
    ):
        result = recon_module.call_agent(
            str(tmp_path), "test/model", "query", [], "agent", max_turns=1
        )

    assert result.error == str(authentication_error)
    assert "Authentication failed" in result.error
    assert not any(
        "Recon agent returned empty result" in record.getMessage()
        and "ending=truncated:length" in record.getMessage()
        for record in caplog.records
    )
