"""LLM tuning configuration — typed loader from config.yml.

FILE: snodo/infrastructure/config.py

Lives in infrastructure so the ENGINE can import it (no engine → cli dep).
Reuses resolve_home() to locate ~/.snodo/config.yml.

The ``llm`` section is optional — absent file or missing keys default to the
current code defaults.
"""

from typing import Dict, Optional

from pydantic import BaseModel, Field, ValidationError

from snodo.infrastructure.paths import resolve_home


class ProviderConfig(BaseModel):
    """Provider configuration with API credential env var and /models endpoint."""
    api_key: str = ""
    api_key_env: str = ""
    models_endpoint: str = ""
    account_id: str = ""
    account_id_env: str = ""
    base_url: str = ""


DEFAULT_PROVIDER_CATALOG: Dict[str, ProviderConfig] = {
    "anthropic": ProviderConfig(
        api_key_env="ANTHROPIC_API_KEY",
        models_endpoint="https://api.anthropic.com/v1/models",
    ),
    "openai": ProviderConfig(
        api_key_env="OPENAI_API_KEY",
        models_endpoint="https://api.openai.com/v1/models",
    ),
    "openrouter": ProviderConfig(
        api_key_env="OPENROUTER_API_KEY",
        models_endpoint="https://openrouter.ai/api/v1/models",
    ),
    "google": ProviderConfig(
        api_key_env="GEMINI_API_KEY",
        models_endpoint="https://generativelanguage.googleapis.com/v1beta/models",
    ),
    "cloudflare": ProviderConfig(
        api_key_env="CLOUDFLARE_API_KEY",
        account_id_env="CLOUDFLARE_ACCOUNT_ID",
        models_endpoint="https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/models",
    ),
    "deepseek": ProviderConfig(
        api_key_env="DEEPSEEK_API_KEY",
        models_endpoint="https://api.deepseek.com/models",
    ),
}


_CODER_MAX_TOKENS_DEFAULT = 16000
_CODER_MAX_TOOL_TURNS_DEFAULT = 6
_VALIDATOR_MAX_TOKENS_DEFAULT = 1500
_VALIDATOR_MAX_TOOL_TURNS_DEFAULT = 6

DEFAULT_MODEL = "claude-sonnet-4-20250514"


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


class LlmConfig(BaseModel):
    coder: CoderConfig = Field(default_factory=CoderConfig)
    validator: ValidatorConfig = Field(default_factory=ValidatorConfig)
    validator_llm: ValidatorLLMConfig = Field(default_factory=ValidatorLLMConfig)


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
