"""Read-only validation of the user configuration file."""

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from snodo.config import ConfigManager, ProviderConfig
from snodo.infrastructure.config import LlmConfig


class _EngineConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_subtask_depth: int = Field(default=3, ge=1, le=10)
    max_session_age_days: int = Field(default=30, ge=1, le=365)
    token_ttl_seconds: int = Field(default=600, ge=60, le=86400)


class _CloudConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_key: str = ""
    api_url: str = "https://api.snodo.dev"
    tunnel_api_url: str = "https://app.snodo.dev"
    sync_enabled: bool = False
    liveness_interval_seconds: int = Field(default=60, ge=1)
    liveness_url: str | None = None
    liveness_api_url: str | None = None
    lease_url: str | None = None
    lease_api_url: str | None = None
    oauth_metadata_url: str | None = None


class _McpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    port: int = Field(default=55441, ge=1, le=65535)


class _OpenCodeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_token_warning: int = 150000
    session_reset_on_model_change: bool = False


class _NotificationsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targets: list[dict[str, Any]] = Field(default_factory=list)
    events: list[str] = Field(default_factory=list)
    silence_threshold_seconds: int = Field(default=900, ge=1)


class _ProviderConfig(ProviderConfig):
    model_config = ConfigDict(extra="forbid")


class _UserConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = None
    default_model: str | None = None
    providers: dict[str, _ProviderConfig] = Field(default_factory=dict)
    engine: _EngineConfig = Field(default_factory=_EngineConfig)
    cloud: _CloudConfig = Field(default_factory=_CloudConfig)
    mcp: _McpConfig = Field(default_factory=_McpConfig)
    opencode: _OpenCodeConfig = Field(default_factory=_OpenCodeConfig)
    llm: LlmConfig = Field(default_factory=LlmConfig)
    notifications: _NotificationsConfig = Field(default_factory=_NotificationsConfig)


_SECRET_PARTS = ("api_key", "token", "secret", "password", "credential")


def validate_config(manager: ConfigManager) -> list[dict[str, str]]:
    """Validate raw user YAML and return safe, path-aware findings."""
    path: Path = manager.config_path
    if not path.exists():
        return []
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        # YAML parser messages can include source snippets; never echo them.
        return [{"path": str(path), "section": "file", "message": "Invalid YAML.", "hint": "Correct the YAML syntax."}]
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        return [{"path": "$", "section": "root", "message": "Expected a mapping.", "hint": "Use top-level key/value sections."}]
    try:
        _UserConfig.model_validate(raw)
        return []
    except ValidationError as exc:
        findings = []
        for error in exc.errors(include_input=False, include_url=False):
            loc = tuple(error.get("loc", ()))
            key_path = ".".join(str(part) for part in loc) or "$"
            section = str(loc[0]) if loc else "root"
            kind = error.get("type", "")
            if kind == "extra_forbidden":
                message = "Unknown configuration key."
                hint = f"Check the spelling and supported keys in the {section} section."
            else:
                message = "Invalid value or type."
                hint = "Check the expected type and constraints for this setting."
            # Error text is deliberately not included: pydantic messages can
            # include field values, and credential-like keys must never leak.
            if any(part.lower().replace("-", "_").endswith(_SECRET_PARTS) for part in loc):
                message = "Invalid secret configuration value."
            findings.append({"path": key_path, "section": section, "message": message, "hint": hint})
        return sorted(findings, key=lambda item: (item["path"], item["message"]))
