"""Read-only validation of the user config using Snodo's strict LLM models."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr, ValidationError

from snodo.config import ConfigManager
from snodo.infrastructure.config import LlmConfig


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Provider(_StrictModel):
    api_key: StrictStr = ""
    api_key_env: StrictStr = ""
    api_key_ref: StrictStr = ""
    models_endpoint: StrictStr = ""
    account_id: StrictStr = ""
    account_id_env: StrictStr = ""
    base_url: StrictStr = ""
    litellm_provider: StrictStr = ""
    extra_headers: dict[StrictStr, StrictStr] = {}
    probe_model: StrictStr = ""
    catalog_provider: StrictStr = ""


class _Engine(_StrictModel):
    max_subtask_depth: StrictInt = 3
    max_session_age_days: StrictInt = 30
    token_ttl_seconds: StrictInt = 600


class _Cloud(_StrictModel):
    api_key: StrictStr = ""
    api_url: StrictStr = "https://api.snodo.dev"
    tunnel_api_url: StrictStr = "https://app.snodo.dev"
    oauth_metadata_url: StrictStr = "https://mcp-auth.snodo.dev/.well-known/oauth-authorization-server"
    sync_enabled: StrictBool = False
    liveness_interval_seconds: StrictInt = 60
    liveness_url: StrictStr | None = None
    liveness_api_url: StrictStr | None = None
    lease_url: StrictStr | None = None
    lease_api_url: StrictStr | None = None


class _NotificationTarget(_StrictModel):
    type: StrictStr
    name: StrictStr = ""
    url: StrictStr
    token: StrictStr = ""
    headers: dict[StrictStr, StrictStr] = {}


class _Notifications(_StrictModel):
    targets: list[_NotificationTarget] = []
    events: list[StrictStr] = ["job_finished", "task_halted", "authorization_needed", "job_silent"]
    silence_threshold_seconds: StrictInt = 900


class _Root(_StrictModel):
    model: StrictStr = "claude-sonnet-4-20250514"
    default_model: StrictStr | None = None
    providers: dict[StrictStr, _Provider] = {}
    engine: _Engine = _Engine()
    llm: LlmConfig = LlmConfig()
    cloud: _Cloud = _Cloud()
    notifications: _Notifications = _Notifications()
    mcp: dict[str, Any] = {}
    opencode: dict[str, Any] = {}


def validate_config(manager: ConfigManager) -> list[dict[str, str]]:
    """Return safe, path-specific findings; never include config values."""
    path = Path(manager.config_path)
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return [_finding("$", "config", "Configuration is not readable valid YAML.", "Fix YAML syntax and run validation again.")]
    if not isinstance(data, dict):
        return [_finding("$", "config", "Configuration must be a mapping.", "Put settings under top-level keys.")]

    findings: list[dict[str, str]] = []
    try:
        _Root.model_validate(data)
    except ValidationError as exc:
        for error in exc.errors():
            loc = error.get("loc", ())
            key_path = ".".join(str(part) for part in loc) or "$"
            section = str(loc[0]) if loc else "config"
            if error.get("type") == "extra_forbidden":
                message, hint = "Unknown configuration key.", "Check the key spelling and the configuration reference."
            else:
                message, hint = "Value has an invalid type or is outside its allowed range.", "Check the expected type and allowed values for this setting."
            findings.append(_finding(key_path, section, message, hint))
    return sorted(findings, key=lambda item: (item["path"], item["message"]))


def _finding(path: str, section: str, message: str, hint: str) -> dict[str, str]:
    return {"path": path, "section": section, "message": message, "hint": hint}
