"""Tests for infrastructure/config.py — LLM config loader."""

import tempfile
import warnings
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel
from snodo.infrastructure.config import (
    ConfigLoadError,
    LlmConfig,
    load_llm_config,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]


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
            "    timeout_seconds: 300\n"
            "  validator:\n"
            "    max_tokens: 500\n"
            "    max_tool_turns: 3\n"
        )
        cfg = load_llm_config(config_dir=tmpdir)
        assert cfg.coder.max_tokens == 8000
        assert cfg.coder.max_tool_turns == 10
        assert cfg.coder.timeout_seconds == 300
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


def test_role_models_load_from_config():
    """Every role model (coder, validator, classifier) is a typed, loadable string.

    Model names are not checked against any catalog — a self-hosted model is
    legitimate and must survive the load untouched.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text(
            "llm:\n"
            "  coder:\n"
            "    model: ollama/qwen3:32b\n"
            "  validator:\n"
            "    model: deepseek/deepseek-chat\n"
            "  classifier:\n"
            "    model: google/gemini-2.5-flash\n"
        )
        cfg = load_llm_config(config_dir=tmpdir)
        assert cfg.coder.model == "ollama/qwen3:32b"
        assert cfg.validator.model == "deepseek/deepseek-chat"
        assert cfg.classifier.model == "google/gemini-2.5-flash"


def test_role_models_default_to_none():
    """Absent role models default to None (= use the top-level model)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text("model: gpt-4o\n")
        cfg = load_llm_config(config_dir=tmpdir)
        assert cfg.coder.model is None
        assert cfg.validator.model is None
        assert cfg.classifier.model is None


# ========== unknown keys under an engine-owned section ==========


@pytest.mark.parametrize(
    "section,unknown",
    [
        ("validator", "single_max_tokens"),
        ("validator", "temperature"),
        ("coder", "temprature"),
        ("classifier", "singel_max_tokens"),
    ],
)
def test_unknown_key_is_reported_with_name_and_section(section, unknown):
    """A key that is not a field of the section is rejected, naming both.

    The reported failure: a validator section carried ``single_max_tokens``
    and ``temperature``, neither a field of ValidatorConfig, and both were
    dropped without a word.  A typo and a knob that was never implemented must
    not look identical.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text(
            "llm:\n"
            f"  {section}:\n"
            f"    {unknown}: 1\n"
        )
        with pytest.raises(ConfigLoadError) as exc_info:
            load_llm_config(config_dir=tmpdir)
        message = str(exc_info.value)
        assert unknown in message
        assert f"llm.{section}" in message


def test_unknown_top_level_key_under_llm_is_reported():
    """A key that is not one of LlmConfig's fields is rejected too."""
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text(
            "llm:\n"
            "  num_retires: 5\n"
        )
        with pytest.raises(ConfigLoadError) as exc_info:
            load_llm_config(config_dir=tmpdir)
        assert "num_retires" in str(exc_info.value)


def _forbidding_llm_sections() -> dict:
    """Every ``llm`` model that forbids extras, keyed by section name.

    The top-level model is keyed ``""`` (the section *is* ``llm``); a nested
    model is keyed by its field name (``coder``, ``validator``, …).  Derived
    from ``LlmConfig`` so a model added to the section cannot escape the
    rejection test below.
    """
    sections = {"": LlmConfig}
    for name, field in LlmConfig.model_fields.items():
        annotation = field.annotation
        if (
            isinstance(annotation, type)
            and issubclass(annotation, BaseModel)
            and annotation.model_config.get("extra") == "forbid"
        ):
            sections[name] = annotation
    return sections


def test_every_llm_section_still_forbids_extras():
    """No model in the ``llm`` section silently drops an unknown key.

    The reported failure: ``llm.wave`` accepted ``max_tokens``/``temperature``
    for weeks because its model ignored extras; a strictness that is relaxed
    for one section is a strictness an operator cannot trust for any of them.
    Enumerating the section's models means a new model cannot be added without
    either forbidding extras or failing here.
    """
    strict = _forbidding_llm_sections()
    nested = {
        name
        for name, field in LlmConfig.model_fields.items()
        if isinstance(field.annotation, type)
        and issubclass(field.annotation, BaseModel)
    }
    assert set(strict) == {""} | nested, (
        "an llm sub-model does not set extra='forbid': "
        f"{sorted(nested - set(strict))}"
    )


@pytest.mark.parametrize(
    "section",
    sorted(_forbidding_llm_sections()),
)
def test_unknown_key_is_rejected_under_every_forbidding_section(section):
    """Every section that forbids extras rejects an unknown key, naming both.

    The rejection must not vary by section: the operator who mistyped a
    ``recon`` key must get the same "not a setting of section ..." message as
    the operator who mistyped a ``coder`` key, or the guard is only as good as
    the section that happens to be covered.
    """
    unknown = f"not_a_{section or 'llm'}_setting"
    body = f"llm:\n  {unknown}: 1\n" if section == "" else (
        f"llm:\n  {section}:\n    {unknown}: 1\n"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text(body)
        with pytest.raises(ConfigLoadError) as exc_info:
            load_llm_config(config_dir=tmpdir)
    message = str(exc_info.value)
    assert unknown in message
    # The section that owns the key is named, so the operator knows where to look.
    if section:
        assert f"llm.{section}." in message
    else:
        assert f"llm.{unknown}" in message


def test_valid_config_produces_no_output(capsys):
    """A fully valid section loads silently — no warning, no stderr, no stdout.

    The new strictness must not turn every run into a lecture: an operator who
    set only real keys sees exactly what they saw before.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        Path(tmpdir, "config.yml").write_text(
            "llm:\n"
            "  coder:\n"
            "    max_tokens: 8000\n"
            "  validator:\n"
            "    max_tool_turns: 3\n"
            "  classifier:\n"
            "    temperature: 0.5\n"
            "  wave:\n"
            "    max_age_days: 30\n"
            "  recon:\n"
            "    num_agents: 2\n"
        )
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            cfg = load_llm_config(config_dir=tmpdir)
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""
        assert cfg.coder.max_tokens == 8000
        assert cfg.validator.max_tool_turns == 3
        assert cfg.classifier.temperature == 0.5


def test_deprecated_wave_migration_is_not_an_unknown_key():
    """The migrated ``llm.wave`` keys are moved before validation, not rejected.

    A supported migration must keep working: the wave keys are popped and
    reported as deprecated, never surfaced as unknown ``llm.wave`` keys.
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


# ========== what snodo itself writes or documents must still load ==========


def test_default_config_written_by_the_tooling_loads():
    """The config a fresh install carries loads under the strict models.

    ``ConfigManager.load()`` is what init and the CLI read into, and
    ``save()`` is what every ``config set`` writes back.  If the default shape
    ever gains a key the ``llm`` models reject, every operator on a fresh
    install hits it on their first run — this is the fault pinned here.
    """
    from snodo.config import ConfigManager

    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = ConfigManager(config_dir=Path(tmpdir))
        mgr.save(mgr.load())
        load_llm_config(config_dir=tmpdir)  # must not raise


def test_every_settable_llm_key_survives_the_cli_and_loads():
    """Each key ``snodo config set`` accepts produces a config that loads.

    The CLI's settable surface (``_LLM_SETTABLE_KEYS``) and the strict models
    must not drift: the day a key is added to the CLI that the model does not
    carry, ``snodo config set`` writes a file that the next run rejects — a
    self-inflicted ``ConfigLoadError`` an operator cannot escape without
    editing the YAML by hand.
    """
    from snodo.cli.commands.config_cmd import _LLM_SETTABLE_KEYS, _set_llm_value
    from snodo.config import ConfigManager

    sample_values = {int: "2", float: "0.5", list: "a,b"}
    with tempfile.TemporaryDirectory() as tmpdir:
        mgr = ConfigManager(config_dir=Path(tmpdir))
        for key, value_type in _LLM_SETTABLE_KEYS.items():
            value = sample_values.get(value_type, "some/model")
            assert _set_llm_value(mgr, key, value) == 0, f"config set llm.{key} failed"
            load_llm_config(config_dir=tmpdir)  # must not raise


def test_documented_runbook_config_loads():
    """The full config example the runbook prints to operators loads.

    The runbook is the contract with the operator: what it tells them to write
    must be exactly what the loader accepts.  A doc that drifts from the model
    turns an operator following it into a failed run; this is the fault pinned.
    """
    runbook = _REPO_ROOT / "docs" / "runbook.md"
    blocks = [
        parsed
        for block in _yaml_blocks(runbook.read_text())
        if isinstance(parsed := yaml.safe_load(block), dict) and "llm" in parsed
    ]
    assert blocks, "runbook no longer contains a full config.yml example"

    with tempfile.TemporaryDirectory() as tmpdir:
        for block in blocks:
            Path(tmpdir, "config.yml").write_text(yaml.safe_dump(block))
            load_llm_config(config_dir=tmpdir)  # must not raise


def _yaml_blocks(text: str) -> list:
    """Return the body of every ```yaml fenced block in *text*."""
    import re

    return re.findall(r"```yaml\n(.*?)```", text, re.DOTALL)
