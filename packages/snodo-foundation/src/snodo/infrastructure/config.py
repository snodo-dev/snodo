"""LLM tuning configuration — typed loader from config.yml.

FILE: snodo/infrastructure/config.py

Lives in infrastructure so the ENGINE can import it (no engine → cli dep).
Reuses resolve_home() to locate ~/.snodo/config.yml.

The ``llm`` section is optional — absent file or missing keys default to the
current code defaults.
"""

import sys
import warnings
from typing import Optional

from pydantic import BaseModel, Field, ValidationError

from snodo.paths import resolve_home
from snodo.config import ProviderConfig, DEFAULT_PROVIDER_CATALOG, DEFAULT_MODEL  # noqa: F401

__all__ = [
    "ProviderConfig",
    "DEFAULT_PROVIDER_CATALOG",
    "DEFAULT_MODEL",
    "load_llm_config",
    "ConfigLoadError",
    "CoderConfig",
    "ValidatorConfig",
    "ClassifierConfig",
    "WaveConfig",
    "LlmConfig",
]

_CODER_MAX_TOKENS_DEFAULT = 16000
_CODER_MAX_TOOL_TURNS_DEFAULT = 6
_CODER_TIMEOUT_SECONDS_DEFAULT = 1800
_CODER_CONCURRENCY_DEFAULT = 1
_VALIDATOR_MAX_TOKENS_DEFAULT = 1500
_VALIDATOR_MAX_TOOL_TURNS_DEFAULT = 6


class ConfigLoadError(Exception):
    """Raised when config.yml exists but cannot be loaded (malformed YAML or validation error)."""


class CoderConfig(BaseModel):
    max_tokens: int = Field(default=_CODER_MAX_TOKENS_DEFAULT, ge=1)
    max_tool_turns: int = Field(default=_CODER_MAX_TOOL_TURNS_DEFAULT, ge=1, le=200)
    timeout_seconds: int = Field(default=_CODER_TIMEOUT_SECONDS_DEFAULT, ge=1)
    concurrency: int = Field(
        default=_CODER_CONCURRENCY_DEFAULT,
        ge=1,
        description="Maximum concurrent coders this machine / operator can carry (default 1).",
    )


class ValidatorConfig(BaseModel):
    max_tokens: int = Field(default=_VALIDATOR_MAX_TOKENS_DEFAULT, ge=1)
    max_tool_turns: int = Field(default=_VALIDATOR_MAX_TOOL_TURNS_DEFAULT, ge=1, le=200)
    model: Optional[str] = Field(default=None, description="Validator LLM model. None = use default_model.")


class ValidatorLLMConfig(BaseModel):
    model: Optional[str] = Field(default=None, description="Validator LLM model. None = use default_model.")


class ClassifierConfig(BaseModel):
    model: Optional[str] = Field(default=None, description="Classifier LLM model. None = use default_model.")
    max_tokens: int = Field(default=500, ge=1, description="Max tokens for classifier completion")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0, description="Temperature for classifier completion")


class ReconConfig(BaseModel):
    num_agents: int = Field(default=1, ge=1, description="Default number of agents for recon fan-out")
    models: list[str] = Field(default_factory=list, description="Ordered model priority list for recon")


class WaveConfig(BaseModel):
    max_age_days: int = Field(default=14, ge=1, description="Hard expiry age for a wave")
    max_idle_days: int = Field(default=5, ge=1, description="Idle timeout before wave closes")


class LlmConfig(BaseModel):
    num_retries: int = Field(default=3, ge=0, le=10, description="litellm retry count for transient errors")
    coder: CoderConfig = Field(default_factory=CoderConfig)
    validator: ValidatorConfig = Field(default_factory=ValidatorConfig)
    validator_llm: ValidatorLLMConfig = Field(default_factory=ValidatorLLMConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    recon: ReconConfig = Field(default_factory=ReconConfig)
    wave: WaveConfig = Field(default_factory=WaveConfig)


# The classifier budget/temperature knobs were shipped by accident under
# ``llm.wave`` (commit 9529e4b wired WaveConfig instead of ClassifierConfig,
# contradicting its own spec C3).  They were the only working classifier knobs,
# so they are migrated to ``llm.classifier`` with a deprecation warning rather
# than dropped — silently reverting a user's raised budget to the default is
# not acceptable.  See ADR 020.
_WAVE_CLASSIFIER_KEYS = ("max_tokens", "temperature")
_wave_migration_warned = False


def _migrate_wave_classifier_keys(llm_data: dict) -> dict:
    """Move deprecated ``llm.wave.max_tokens``/``temperature`` to ``llm.classifier``.

    Mutates and returns *llm_data*.  Values already present under
    ``llm.classifier`` win; the deprecated wave values are then discarded (but
    still reported).  Emits a ``DeprecationWarning`` (and a one-time stderr
    notice, since DeprecationWarning is filtered out of normal runtime output)
    so the move is never silent.
    """
    global _wave_migration_warned

    wave = llm_data.get("wave")
    if not isinstance(wave, dict):
        return llm_data

    deprecated = {}
    for key in _WAVE_CLASSIFIER_KEYS:
        if key in wave:
            deprecated[key] = wave.pop(key)
    if not deprecated:
        return llm_data

    classifier = llm_data.setdefault("classifier", {})
    applied = {}
    for key, value in deprecated.items():
        if key not in classifier:
            classifier[key] = value
            applied[key] = value

    msg = (
        "llm.wave.max_tokens and llm.wave.temperature have moved to "
        "llm.classifier.max_tokens and llm.classifier.temperature. "
        f"Deprecated wave keys found: {sorted(deprecated)}. "
    )
    if applied:
        msg += f"Migrated to llm.classifier: {sorted(applied)}. "
    else:
        msg += "llm.classifier already sets these, so the wave values were ignored. "
    msg += "Update your config.yml to the llm.classifier keys."

    warnings.warn(msg, DeprecationWarning, stacklevel=2)
    if not _wave_migration_warned:
        _wave_migration_warned = True
        print(f"[config] {msg}", file=sys.stderr)
    return llm_data


def load_llm_config(config_dir: Optional[str] = None) -> LlmConfig:
    """Load ``llm`` section from config.yml, returning defaults when absent.

    Args:
        config_dir: Optional override for the snodo home directory.

    Returns:
        LlmConfig populated from config.yml when present, otherwise defaults.

    Raises:
        ConfigLoadError: If config.yml exists but contains malformed YAML
            or fails pydantic validation.
    """
    import yaml
    from pathlib import Path as _Path

    home = resolve_home() if config_dir is None else _Path(config_dir)
    config_path = home / "config.yml"
    if not config_path.exists():
        return LlmConfig()

    try:
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        return LlmConfig()
    except yaml.YAMLError as e:
        raise ConfigLoadError(
            f"Malformed YAML in {config_path}: {e}"
        ) from e

    llm_data = data.get("llm") if isinstance(data, dict) else None
    if not isinstance(llm_data, dict):
        return LlmConfig()

    llm_data = _migrate_wave_classifier_keys(llm_data)

    try:
        return LlmConfig(**llm_data)
    except ValidationError as e:
        raise ConfigLoadError(
            f"Invalid config in {config_path}: {e}"
        ) from e
