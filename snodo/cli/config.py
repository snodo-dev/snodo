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

from snodo.infrastructure.paths import resolve_home


# Provider-to-model prefix mapping for key resolution
PROVIDER_MODEL_PREFIXES = {
    "openai": ["gpt-", "o1-", "o3-"],
    "anthropic": ["claude-"],
    "google": ["gemini/", "gemini-"],
}

DEFAULT_MODEL = "claude-sonnet-4-20250514"


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
        """
        if not self.config_path.exists():
            return {"api_keys": {}, "model": DEFAULT_MODEL, "engine": {"max_subtask_depth": 3, "max_session_age_days": 30, "token_ttl_seconds": 600}}

        try:
            with open(self.config_path) as f:
                data = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ConfigError(f"Invalid config file: {e}")

        # Ensure required keys exist
        data.setdefault("api_keys", {})
        data.setdefault("model", DEFAULT_MODEL)
        data.setdefault("engine", {"max_subtask_depth": 3, "max_session_age_days": 30, "token_ttl_seconds": 600})
        return data

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
        config["api_keys"][provider] = key
        self.save(config)

    def get_key(self, provider: str) -> Optional[str]:
        """Get an API key for a provider.

        Args:
            provider: Provider name

        Returns:
            API key string, or None if not configured
        """
        config = self.load()
        return config["api_keys"].get(provider)

    def remove_key(self, provider: str) -> bool:
        """Remove an API key for a provider.

        Args:
            provider: Provider name

        Returns:
            True if key was removed, False if it didn't exist
        """
        config = self.load()
        if provider in config["api_keys"]:
            del config["api_keys"][provider]
            self.save(config)
            return True
        return False

    def get_key_for_model(self, model: str) -> Optional[str]:
        """Resolve the API key needed for a given model.

        Args:
            model: Model identifier (e.g., "claude-sonnet-4-20250514", "gpt-4")

        Returns:
            API key string, or None if no matching key found
        """
        config = self.load()
        keys = config.get("api_keys", {})

        for provider, prefixes in PROVIDER_MODEL_PREFIXES.items():
            for prefix in prefixes:
                if model.startswith(prefix):
                    return keys.get(provider)

        # Fallback: check environment or return None
        return None

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
        return config.get("model", DEFAULT_MODEL)

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
        config = self.load()
        keys = config.get("api_keys", {})
        results = {}

        for provider, key in keys.items():
            results[provider] = self._test_single_key(provider, key)

        return results

    def _test_single_key(self, provider: str, key: str) -> bool:
        """Test a single API key by making a minimal LLM call.

        Args:
            provider: Provider name
            key: API key to test

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

            # Set the API key in environment temporarily
            env_key_map = {
                "openai": "OPENAI_API_KEY",
                "anthropic": "ANTHROPIC_API_KEY",
                "google": "GEMINI_API_KEY",
            }

            env_var = env_key_map.get(provider)
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
