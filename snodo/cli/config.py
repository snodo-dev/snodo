"""Configuration and API key management for Snodo.

FILE: snodo/cli/config.py (Task 3.6)

Manages user configuration stored at ~/.snodo/config.yml:
- API key storage with secure file permissions (600)
- Model selection defaults
- Key validation via liteLLM
"""

import os
import stat
from pathlib import Path
from typing import Any, Dict, Optional

import yaml
from pydantic import BaseModel

from snodo.infrastructure.paths import resolve_home
from snodo.infrastructure.config import DEFAULT_MODEL


class ProviderConfig(BaseModel):
    """Provider configuration with API credential env var and /models endpoint."""
    api_key_env: str = ""
    models_endpoint: str = ""


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
}


# Provider-to-model prefix mapping for key resolution
PROVIDER_MODEL_PREFIXES = {
    "openai": ["gpt-", "o1-", "o3-"],
    "anthropic": ["claude-"],
    "google": ["gemini/", "gemini-"],
}


def _set_api_key_env(mgr: "ConfigManager", model: str) -> None:
    """Set API key in environment if available from config."""
    api_key = mgr.get_key_for_model(model)
    if api_key:
        provider_name = ConfigManager._provider_for_model(model)
        if provider_name:
            providers = mgr.get_providers()
            pc = providers.get(provider_name)
            if pc and pc.api_key_env:
                os.environ[pc.api_key_env] = api_key


class ConfigError(Exception):
    """Configuration error."""


class ConfigManager:
    """Manages Snodo user configuration.

    Config file: ~/.snodo/config.yml
    Enforces 600 permissions on the config file.
    """

    def __init__(self, config_dir: Optional[Path] = None):
        """Initialize config manager.

        Args:
            config_dir: Override config directory (default: ~/.snodo)
        """
        self.config_dir = config_dir or resolve_home()
        self.config_path = self.config_dir / "config.yml"

    def load(self) -> dict:
        """Load configuration from disk.

        Returns:
            Configuration dict (empty dict if file doesn't exist)

        Raises:
            ConfigError: If config.yml uses the legacy ``api_keys`` format
                instead of the ``providers`` section.
        """
        if not self.config_path.exists():
            return self._default_config()

        try:
            with open(self.config_path) as f:
                data = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"Invalid config file: {e}")

        # Reject legacy api_keys-only configs — providers is the only schema
        if data.get("api_keys") and not data.get("providers"):
            raise ConfigError(
                "Legacy api_keys config detected. "
                "Migrate to the providers section. See docs."
            )

        # Ensure required keys exist
        data.setdefault("model", DEFAULT_MODEL)
        data.setdefault("engine", {"max_subtask_depth": 3, "max_session_age_days": 30, "token_ttl_seconds": 600})
        return data

    def _default_config(self) -> dict:
        return {
            "model": DEFAULT_MODEL,
            "engine": {"max_subtask_depth": 3, "max_session_age_days": 30, "token_ttl_seconds": 600},
        }

    def get_providers(self) -> Dict[str, ProviderConfig]:
        """Return configured providers, merged with defaults.

        Config.yml ``providers`` section overrides default catalog entries.
        Unlisted providers get their default values.
        """
        config = self.load()
        providers_raw = config.get("providers", {})

        result: Dict[str, ProviderConfig] = {}
        if isinstance(providers_raw, dict):
            for name, raw in providers_raw.items():
                if isinstance(raw, dict):
                    result[name] = ProviderConfig(**raw)

        # Merge in defaults for providers not in config
        for name, pc in DEFAULT_PROVIDER_CATALOG.items():
            if name not in result:
                result[name] = pc
        return result

    @staticmethod
    def _provider_for_model(model: str) -> Optional[str]:
        """Return the provider name for a given model string prefix."""
        for provider, prefixes in PROVIDER_MODEL_PREFIXES.items():
            for prefix in prefixes:
                if model.startswith(prefix):
                    return provider
        return None

    def save(self, config: dict) -> None:
        """Save configuration to disk with secure permissions.

        Args:
            config: Configuration dict to save
        """
        self.config_dir.mkdir(parents=True, exist_ok=True)

        with open(self.config_path, "w") as f:
            yaml.dump(config, f, default_flow_style=False)

        # Set file permissions to 600 (owner read/write only)
        os.chmod(self.config_path, stat.S_IRUSR | stat.S_IWUSR)

    def add_key(self, provider: str, key: str) -> None:
        """Store an API key for a provider.

        Args:
            provider: Provider name (e.g., "openai", "anthropic", "google")
            key: API key string
        """
        if not provider:
            raise ConfigError("Provider name cannot be empty")
        if not key:
            raise ConfigError("API key cannot be empty")

        config = self.load()
        providers = config.setdefault("providers", {})
        provider_data = providers.setdefault(provider, {})
        provider_data["api_key"] = key
        self.save(config)

    def get_key(self, provider: str) -> Optional[str]:
        """Get an API key for a provider.

        Args:
            provider: Provider name

        Returns:
            API key string, or None if not configured
        """
        config = self.load()
        providers_raw = config.get("providers", {})
        if isinstance(providers_raw, dict):
            entry = providers_raw.get(provider, {})
            if isinstance(entry, dict):
                return entry.get("api_key")
        return None

    def remove_key(self, provider: str) -> bool:
        """Remove an API key for a provider.

        Args:
            provider: Provider name

        Returns:
            True if key was removed, False if it didn't exist
        """
        config = self.load()
        providers_raw = config.get("providers", {})
        if isinstance(providers_raw, dict):
            entry = providers_raw.get(provider, {})
            if isinstance(entry, dict) and "api_key" in entry:
                del entry["api_key"]
                self.save(config)
                return True
        return False

    def get_key_for_model(self, model: str) -> Optional[str]:
        """Resolve the API key needed for a given model.

        Args:
            model: Model identifier (e.g., "claude-sonnet-4-20250514", "gpt-4o")

        Returns:
            API key string, or None if no matching key found
        """
        provider = self._provider_for_model(model)
        if provider is None:
            return None
        return self.get_key(provider)

    def set_model(self, model: str) -> None:
        """Set the default model.

        Args:
            model: Model identifier
        """
        config = self.load()
        config["model"] = model
        self.save(config)

    def get_model(self) -> str:
        """Get the configured default model.

        Returns:
            Model identifier
        """
        config = self.load()
        return config.get("default_model") or config.get("model", DEFAULT_MODEL)

    def get_engine_value(self, key: str, default: Any = None) -> Any:
        """Get an engine configuration value.

        Args:
            key: Engine config key (e.g., "max_subtask_depth")
            default: Default value if key not found

        Returns:
            Config value
        """
        config = self.load()
        return config.get("engine", {}).get(key, default)

    def set_engine_value(self, key: str, value: Any) -> None:
        """Set an engine configuration value.

        Args:
            key: Engine config key
            value: Value to set

        Raises:
            ValueError: If value fails validation
        """
        if key == "max_subtask_depth":
            if not isinstance(value, int) or value < 1 or value > 10:
                raise ValueError(f"max_subtask_depth must be an integer between 1 and 10, got {value}")
        elif key == "max_session_age_days":
            if not isinstance(value, int) or value < 1 or value > 365:
                raise ValueError(f"max_session_age_days must be an integer between 1 and 365, got {value}")
        elif key == "token_ttl_seconds":
            if not isinstance(value, int) or value < 60 or value > 86400:
                raise ValueError(f"token_ttl_seconds must be an integer between 60 and 86400, got {value}")

        config = self.load()
        engine = config.setdefault("engine", {})
        engine[key] = value
        self.save(config)

    def test_keys(self) -> Dict[str, bool]:
        """Test all configured API keys via liteLLM.

        Returns:
            Dict of provider -> success boolean
        """
        results = {}
        providers = self.get_providers()

        for name, pc in providers.items():
            key = self.get_key(name)
            if not key:
                continue
            results[name] = self._test_single_key(name, key, pc)

        return results

    def _test_single_key(self, provider: str, key: str, pc: Optional[ProviderConfig] = None) -> bool:
        """Test a single API key by making a minimal LLM call.

        Args:
            provider: Provider name
            key: API key to test
            pc: ProviderConfig with api_key_env

        Returns:
            True if key is valid
        """
        try:
            from litellm import completion

            # Map provider to a cheap test model
            test_models = {
                "openai": "gpt-4o-mini",
                "anthropic": "claude-sonnet-4-20250514",
                "google": "gemini/gemini-2.0-flash-exp",
            }

            model = test_models.get(provider)
            if not model:
                return False

            if pc is not None and pc.api_key_env:
                env_var = pc.api_key_env
            else:
                # Fallback for backward-compat direct calls without ProviderConfig
                env_map = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "google": "GEMINI_API_KEY"}
                env_var = env_map.get(provider, "")
            if not env_var:
                return False

            old_val = os.environ.get(env_var)
            try:
                os.environ[env_var] = key
                completion(
                    model=model,
                    messages=[{"role": "user", "content": "hi"}],
                    max_tokens=1,
                )
                return True
            finally:
                if old_val is not None:
                    os.environ[env_var] = old_val
                elif env_var in os.environ:
                    del os.environ[env_var]

        except Exception:
            return False

    @staticmethod
    def mask_key(key: str) -> str:
        """Mask an API key for display, showing only prefix and suffix.

        Args:
            key: Full API key

        Returns:
            Masked key (e.g., "sk-ab...xyz")
        """
        if len(key) <= 8:
            return key[:2] + "***"
        return key[:5] + "***" + key[-3:]
