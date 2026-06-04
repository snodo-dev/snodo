"""LLM tuning configuration — typed loader from config.yml.

FILE: snodo/infrastructure/config.py

Lives in infrastructure so the ENGINE can import it (no engine → cli dep).
Reuses resolve_home() to locate ~/.snodo/config.yml.

The ``llm`` section is optional — absent file or missing keys default to the
current code defaults.
"""

from typing import Optional

from pydantic import BaseModel, Field

from snodo.infrastructure.paths import resolve_home

_CODER_MAX_TOKENS_DEFAULT = 16000
_CODER_MAX_TOOL_TURNS_DEFAULT = 6
_VALIDATOR_MAX_TOKENS_DEFAULT = 1500
_VALIDATOR_MAX_TOOL_TURNS_DEFAULT = 6


class CoderConfig(BaseModel):
    max_tokens: int = Field(default=_CODER_MAX_TOKENS_DEFAULT, ge=1)
    max_tool_turns: int = Field(default=_CODER_MAX_TOOL_TURNS_DEFAULT, ge=1, le=20)


class ValidatorConfig(BaseModel):
    max_tokens: int = Field(default=_VALIDATOR_MAX_TOKENS_DEFAULT, ge=1)
    max_tool_turns: int = Field(default=_VALIDATOR_MAX_TOOL_TURNS_DEFAULT, ge=1, le=20)


class LlmConfig(BaseModel):
    coder: CoderConfig = Field(default_factory=CoderConfig)
    validator: ValidatorConfig = Field(default_factory=ValidatorConfig)


def load_llm_config(config_dir: Optional[str] = None) -> LlmConfig:
    """Load ``llm`` section from config.yml, returning defaults when absent.

    Args:
        config_dir: Optional override for the snodo home directory.

    Returns:
        LlmConfig populated from config.yml when present, otherwise defaults.
    """
    try:
        import yaml

        home = resolve_home() if config_dir is None else __import__("pathlib").Path(config_dir)
        config_path = home / "config.yml"
        if not config_path.exists():
            return LlmConfig()

        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
    except Exception:
        return LlmConfig()

    llm_data = data.get("llm") if isinstance(data, dict) else None
    if not isinstance(llm_data, dict):
        return LlmConfig()

    try:
        return LlmConfig(**llm_data)
    except Exception:
        return LlmConfig()
