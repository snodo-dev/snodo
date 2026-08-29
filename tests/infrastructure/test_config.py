"""Tests for infrastructure/config.py — LLM config loader."""

import tempfile
from pathlib import Path

import pytest
from snodo.infrastructure.config import (
    ConfigLoadError,
    LlmConfig,
    load_llm_config,
)


def test_missing_file_returns_defaults():
    """When config.yml does not exist, defaults are returned silently."""
    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = load_llm_config(config_dir=tmpdir)
        assert isinstance(cfg, LlmConfig)
        assert cfg.coder.max_tokens == 16000
        assert cfg.validator.max_tokens == 1500


def test_empty_file_returns_defaults():
    """When config.yml exists but is empty, defaults are returned."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text("")
        cfg = load_llm_config(config_dir=tmpdir)
        assert isinstance(cfg, LlmConfig)


def test_file_without_llm_section_returns_defaults():
    """When config.yml has no 'llm' key, defaults are returned."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text("other_key: value\n")
        cfg = load_llm_config(config_dir=tmpdir)
        assert isinstance(cfg, LlmConfig)


def test_valid_config_is_loaded():
    """When config.yml has a valid 'llm' section, values are loaded."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text(
            "llm:\n"
            "  coder:\n"
            "    max_tokens: 8000\n"
            "    max_tool_turns: 10\n"
            "  validator:\n"
            "    max_tokens: 500\n"
            "    max_tool_turns: 3\n"
        )
        cfg = load_llm_config(config_dir=tmpdir)
        assert cfg.coder.max_tokens == 8000
        assert cfg.coder.max_tool_turns == 10
        assert cfg.validator.max_tokens == 500
        assert cfg.validator.max_tool_turns == 3


def test_malformed_yaml_raises_config_load_error():
    """Malformed YAML raises ConfigLoadError with file path in message."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text(
            "llm:\n"
            "  coder:\n"
            "    max_tokens: [invalid yaml\n"
        )
        with pytest.raises(ConfigLoadError) as exc_info:
            load_llm_config(config_dir=tmpdir)
        assert "config.yml" in str(exc_info.value)


def test_invalid_field_value_raises_config_load_error():
    """Pydantic validation failure raises ConfigLoadError with field name."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text(
            "llm:\n"
            "  coder:\n"
            '    max_tokens: "not_an_integer"\n'
        )
        with pytest.raises(ConfigLoadError) as exc_info:
            load_llm_config(config_dir=tmpdir)
        assert "max_tokens" in str(exc_info.value)


def test_out_of_range_value_raises_config_load_error():
    """Out-of-range value (ge=1) raises ConfigLoadError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text(
            "llm:\n"
            "  coder:\n"
            "    max_tokens: 0\n"
        )
        with pytest.raises(ConfigLoadError):
            load_llm_config(config_dir=tmpdir)


def test_partial_config_uses_defaults_for_missing_keys():
    """Partial config fills in missing keys with defaults."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text(
            "llm:\n"
            "  coder:\n"
            "    max_tokens: 4000\n"
        )
        cfg = load_llm_config(config_dir=tmpdir)
        assert cfg.coder.max_tokens == 4000
        assert cfg.coder.max_tool_turns == 6  # default
        assert cfg.validator.max_tokens == 1500  # default


def test_wave_classifier_keys_migrate_with_deprecation_warning():
    """Old llm.wave.max_tokens/temperature migrate to llm.classifier, with a warning.

    These were the only working classifier knobs before ADR 020; silently
    reverting them to defaults is not acceptable, so they are migrated.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text(
            "llm:\n"
            "  wave:\n"
            "    max_tokens: 2000\n"
            "    temperature: 0.5\n"
        )
        with pytest.warns(DeprecationWarning):
            cfg = load_llm_config(config_dir=tmpdir)
        assert cfg.classifier.max_tokens == 2000
        assert cfg.classifier.temperature == 0.5
        # WaveConfig no longer carries the classifier knobs.
        assert not hasattr(cfg.wave, "max_tokens")
        assert not hasattr(cfg.wave, "temperature")


def test_classifier_wins_over_deprecated_wave_keys():
    """When both llm.wave and llm.classifier set a knob, classifier wins."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text(
            "llm:\n"
            "  wave:\n"
            "    max_tokens: 2000\n"
            "  classifier:\n"
            "    max_tokens: 3000\n"
        )
        with pytest.warns(DeprecationWarning):
            cfg = load_llm_config(config_dir=tmpdir)
        assert cfg.classifier.max_tokens == 3000


def test_wave_lifetime_keys_do_not_warn():
    """llm.wave.max_age_days/max_idle_days remain valid and do not warn."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text(
            "llm:\n"
            "  wave:\n"
            "    max_age_days: 30\n"
            "    max_idle_days: 10\n"
        )
        cfg = load_llm_config(config_dir=tmpdir)
        assert cfg.wave.max_age_days == 30
        assert cfg.wave.max_idle_days == 10
