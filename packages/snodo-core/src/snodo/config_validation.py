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


class ConfigKeyError(ValueError):
    """A dotted config path is unknown or its value fails runtime validation."""


_EDITABLE_SECTIONS = {"providers", "cloud", "notifications"}
_SECRET_CONFIG_PATHS = {
    ("providers", "*", "api_key"),
    ("providers", "*", "api_key_env"),
    ("providers", "*", "api_key_ref"),
    ("cloud", "api_key"),
    ("notifications", "targets", "*", "url"),
    ("notifications", "targets", "*", "token"),
    ("notifications", "targets", "*", "headers", "*"),
}


def is_secret_config_key(key_path: str) -> bool:
    """Return whether a supported config path may contain credentials."""
    parts = tuple(key_path.split("."))
    return any(
        len(parts) == len(pattern)
        and all(expected == "*" or expected == actual for expected, actual in zip(pattern, parts))
        for pattern in _SECRET_CONFIG_PATHS
    )


def get_config_value(manager: ConfigManager, key_path: str) -> Any:
    """Read a provider/cloud/notification leaf through the strict runtime schema."""
    parts = _config_key_parts(key_path)
    data = _validated_config(manager.load())
    value: Any = data
    for part in parts:
        if isinstance(value, list):
            try:
                value = value[int(part)]
            except (ValueError, IndexError):
                raise ConfigKeyError(f"Unknown config key: {key_path}") from None
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise ConfigKeyError(f"Unknown config key: {key_path}")
    if isinstance(value, dict):
        raise ConfigKeyError(f"Config key must name a value: {key_path}")
    return value


def set_config_value(manager: ConfigManager, key_path: str, value: Any) -> None:
    """Validate and write a provider/cloud/notification leaf by dotted path."""
    parts = _config_key_parts(key_path)
    raw = manager.load()
    if parts[0] == "providers" and len(parts) == 3:
        if parts[2] not in _Provider.model_fields:
            raise ConfigKeyError(f"Unknown config key: {key_path}")
        provider = raw.setdefault("providers", {}).setdefault(parts[1], {})
        if not isinstance(provider, dict):
            raise ConfigKeyError(f"Invalid config value at providers.{parts[1]}")
        provider[parts[2]] = value
    try:
        candidate = _validated_config(raw)
    except ConfigKeyError as exc:
        raise ConfigKeyError(f"Invalid value for config key {key_path}: {exc}") from exc
    cursor: Any = candidate
    for part in parts[:-1]:
        if isinstance(cursor, list):
            try:
                cursor = cursor[int(part)]
            except (ValueError, IndexError):
                raise ConfigKeyError(f"Unknown config key: {key_path}") from None
        elif isinstance(cursor, dict) and part in cursor:
            cursor = cursor[part]
        else:
            raise ConfigKeyError(f"Unknown config key: {key_path}")
    last = parts[-1]
    if isinstance(cursor, list):
        try:
            cursor[int(last)] = value
        except (ValueError, IndexError):
            raise ConfigKeyError(f"Unknown config key: {key_path}") from None
    elif isinstance(cursor, dict) and last in cursor:
        cursor[last] = value
    elif isinstance(cursor, dict) and parts[0] == "providers" and len(parts) == 3 and last in _Provider.model_fields:
        cursor[last] = value
    else:
        raise ConfigKeyError(f"Unknown config key: {key_path}")
    try:
        _validated_config(candidate)
    except ConfigKeyError as exc:
        raise ConfigKeyError(f"Invalid value for config key {key_path}: {exc}") from exc
    manager.set_value(parts, value)


def _config_key_parts(key_path: str) -> tuple[str, ...]:
    parts = tuple(key_path.split("."))
    if len(parts) < 2 or parts[0] not in _EDITABLE_SECTIONS or any(not part for part in parts):
        raise ConfigKeyError(f"Unknown config key: {key_path}")
    # Provider names are dynamic; permit a previously absent provider, but only
    # when its field is a declared Provider model field.
    if parts[0] == "providers" and len(parts) == 3 and parts[2] in _Provider.model_fields:
        return parts
    return parts


def _validated_config(data: dict) -> dict:
    try:
        return _Root.model_validate(data).model_dump(mode="python")
    except ValidationError as exc:
        unknown = next((e for e in exc.errors() if e.get("type") == "extra_forbidden"), None)
        if unknown:
            path = ".".join(str(part) for part in unknown.get("loc", ()))
            raise ConfigKeyError(f"Unknown config key: {path}") from exc
        raise ConfigKeyError(f"Invalid config value: {exc}") from exc


def _finding(path: str, section: str, message: str, hint: str) -> dict[str, str]:
    return {"path": path, "section": section, "message": message, "hint": hint}
