"""LLM tuning configuration — typed loader from config.yml.

FILE: snodo/infrastructure/config.py

Lives in infrastructure so the ENGINE can import it (no engine → cli dep).
Reuses resolve_home() to locate ~/.snodo/config.yml.

The ``llm`` section is optional — absent file or missing keys default to the
current code defaults.
"""

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
    "LlmConfig",
]

_CODER_MAX_TOKENS_DEFAULT = 16000
_CODER_MAX_TOOL_TURNS_DEFAULT = 6
_VALIDATOR_MAX_TOKENS_DEFAULT = 1500
_VALIDATOR_MAX_TOOL_TURNS_DEFAULT = 6


class ConfigLoadError(Exception):
    """Raised when config.yml exists but cannot be loaded (malformed YAML or validation error)."""


class CoderConfig(BaseModel):
    max_tokens: int = Field(default=_CODER_MAX_TOKENS_DEFAULT, ge=1)
    max_tool_turns: int = Field(default=_CODER_MAX_TOOL_TURNS_DEFAULT, ge=1, le=200)


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
    max_tokens: int = Field(default=500, ge=1, description="Max tokens for classifier completion")
    temperature: float = Field(default=0.0, ge=0.0, le=2.0, description="Temperature for classifier completion")


class LlmConfig(BaseModel):
    num_retries: int = Field(default=3, ge=0, le=10, description="litellm retry count for transient errors")
    coder: CoderConfig = Field(default_factory=CoderConfig)
    validator: ValidatorConfig = Field(default_factory=ValidatorConfig)
    validator_llm: ValidatorLLMConfig = Field(default_factory=ValidatorLLMConfig)
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    recon: ReconConfig = Field(default_factory=ReconConfig)
    wave: WaveConfig = Field(default_factory=WaveConfig)


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

    try:
        return LlmConfig(**llm_data)
    except ValidationError as e:
        raise ConfigLoadError(
            f"Invalid config in {config_path}: {e}"
        ) from e
