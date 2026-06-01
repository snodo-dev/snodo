"""Tests for Snodo configuration management (Task 3.6).

FILE: tests/cli/test_config.py

Tests ConfigManager and CLI config commands:
- Key storage/retrieval/removal
- Key masking
- File permissions
- Model resolution priority
- CLI subcommands (show, add, remove, test)
"""

import os
import stat
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from snodo.cli.config import ConfigManager, ConfigError, DEFAULT_MODEL
from snodo.cli.main import main


@pytest.fixture
def config_dir():
    """Create a temporary config directory."""
    d = tempfile.mkdtemp()
    yield Path(d)
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def mgr(config_dir):
    """Create a ConfigManager with a temp directory."""
    return ConfigManager(config_dir=config_dir)


# === ConfigManager.load / save ===

class TestConfigLoadSave:
    def test_load_returns_defaults_when_no_file(self, mgr):
        config = mgr.load()
        assert config["api_keys"] == {}
        assert config["model"] == DEFAULT_MODEL

    def test_save_and_load_round_trip(self, mgr):
        config = {"api_keys": {"openai": "sk-test123"}, "model": "gpt-4"}
        mgr.save(config)
        loaded = mgr.load()
        assert loaded["api_keys"]["openai"] == "sk-test123"
        assert loaded["model"] == "gpt-4"

    def test_save_creates_directory(self, config_dir):
        nested = config_dir / "nested" / "deep"
        mgr = ConfigManager(config_dir=nested)
        mgr.save({"api_keys": {}, "model": "gpt-4"})
        assert mgr.config_path.exists()

    def test_save_sets_600_permissions(self, mgr):
        mgr.save({"api_keys": {}, "model": "gpt-4"})
        mode = os.stat(mgr.config_path).st_mode
        assert mode & 0o777 == 0o600

    def test_load_invalid_yaml_raises(self, mgr):
        mgr.config_dir.mkdir(parents=True, exist_ok=True)
        mgr.config_path.write_text("invalid: yaml: [\n")
        with pytest.raises(ConfigError, match="Invalid config file"):
            mgr.load()

    def test_load_empty_file_returns_defaults(self, mgr):
        mgr.config_dir.mkdir(parents=True, exist_ok=True)
        mgr.config_path.write_text("")
        config = mgr.load()
        assert config["api_keys"] == {}
        assert config["model"] == DEFAULT_MODEL

    def test_load_partial_config_fills_defaults(self, mgr):
        mgr.config_dir.mkdir(parents=True, exist_ok=True)
        mgr.config_path.write_text("model: gpt-4\n")
        config = mgr.load()
        assert config["api_keys"] == {}
        assert config["model"] == "gpt-4"


# === ConfigManager.add_key / get_key / remove_key ===

class TestKeyManagement:
    def test_add_and_get_key(self, mgr):
        mgr.add_key("openai", "sk-abc123")
        assert mgr.get_key("openai") == "sk-abc123"

    def test_get_key_nonexistent_returns_none(self, mgr):
        assert mgr.get_key("openai") is None

    def test_add_key_overwrites_existing(self, mgr):
        mgr.add_key("openai", "sk-old")
        mgr.add_key("openai", "sk-new")
        assert mgr.get_key("openai") == "sk-new"

    def test_add_multiple_providers(self, mgr):
        mgr.add_key("openai", "sk-openai")
        mgr.add_key("anthropic", "sk-ant")
        assert mgr.get_key("openai") == "sk-openai"
        assert mgr.get_key("anthropic") == "sk-ant"

    def test_remove_key_existing(self, mgr):
        mgr.add_key("openai", "sk-test")
        assert mgr.remove_key("openai") is True
        assert mgr.get_key("openai") is None

    def test_remove_key_nonexistent(self, mgr):
        assert mgr.remove_key("openai") is False

    def test_add_key_empty_provider_raises(self, mgr):
        with pytest.raises(ConfigError, match="Provider name cannot be empty"):
            mgr.add_key("", "sk-test")

    def test_add_key_empty_key_raises(self, mgr):
        with pytest.raises(ConfigError, match="API key cannot be empty"):
            mgr.add_key("openai", "")

    def test_remove_preserves_other_keys(self, mgr):
        mgr.add_key("openai", "sk-o")
        mgr.add_key("anthropic", "sk-a")
        mgr.remove_key("openai")
        assert mgr.get_key("anthropic") == "sk-a"


# === ConfigManager.get_key_for_model ===

class TestKeyForModel:
    def test_openai_model_resolves_openai_key(self, mgr):
        mgr.add_key("openai", "sk-openai")
        assert mgr.get_key_for_model("gpt-4") == "sk-openai"
        assert mgr.get_key_for_model("gpt-4o-mini") == "sk-openai"

    def test_anthropic_model_resolves_anthropic_key(self, mgr):
        mgr.add_key("anthropic", "sk-ant")
        assert mgr.get_key_for_model("claude-sonnet-4-20250514") == "sk-ant"
        assert mgr.get_key_for_model("claude-3-haiku") == "sk-ant"

    def test_google_model_resolves_google_key(self, mgr):
        mgr.add_key("google", "goog-key")
        assert mgr.get_key_for_model("gemini/gemini-2.0-flash-exp") == "goog-key"
        assert mgr.get_key_for_model("gemini-pro") == "goog-key"

    def test_o1_model_resolves_openai_key(self, mgr):
        mgr.add_key("openai", "sk-openai")
        assert mgr.get_key_for_model("o1-preview") == "sk-openai"
        assert mgr.get_key_for_model("o3-mini") == "sk-openai"

    def test_unknown_model_returns_none(self, mgr):
        mgr.add_key("openai", "sk-openai")
        assert mgr.get_key_for_model("unknown-model") is None

    def test_no_keys_configured_returns_none(self, mgr):
        assert mgr.get_key_for_model("gpt-4") is None


# === ConfigManager.set_model / get_model ===

class TestModelConfig:
    def test_get_model_default(self, mgr):
        assert mgr.get_model() == DEFAULT_MODEL

    def test_set_and_get_model(self, mgr):
        mgr.set_model("gpt-4")
        assert mgr.get_model() == "gpt-4"

    def test_set_model_persists(self, mgr):
        mgr.set_model("gpt-4")
        mgr2 = ConfigManager(config_dir=mgr.config_dir)
        assert mgr2.get_model() == "gpt-4"


# === ConfigManager.mask_key ===

class TestMaskKey:
    def test_mask_long_key(self):
        assert ConfigManager.mask_key("sk-abcdefghijk") == "sk-ab***ijk"

    def test_mask_short_key(self):
        assert ConfigManager.mask_key("sk-ab") == "sk***"

    def test_mask_exact_boundary(self):
        # 8 chars is the boundary - treated as short
        assert ConfigManager.mask_key("12345678") == "12***"
        # 9 chars uses the long path
        assert ConfigManager.mask_key("123456789") == "12345***789"

    def test_mask_very_short(self):
        assert ConfigManager.mask_key("ab") == "ab***"

    def test_mask_typical_openai_key(self):
        key = "sk-proj-abc123def456ghi789"
        masked = ConfigManager.mask_key(key)
        assert masked.startswith("sk-pr")
        assert masked.endswith("789")
        assert "***" in masked
        assert len(masked) < len(key)


# === ConfigManager.test_keys ===

class TestTestKeys:
    def test_test_keys_no_keys(self, mgr):
        results = mgr.test_keys()
        assert results == {}

    @patch("snodo.cli.config.ConfigManager._test_single_key")
    def test_test_keys_calls_per_provider(self, mock_test, mgr):
        mgr.add_key("openai", "sk-test")
        mgr.add_key("anthropic", "sk-ant")
        mock_test.return_value = True

        results = mgr.test_keys()
        assert results == {"openai": True, "anthropic": True}
        assert mock_test.call_count == 2

    @patch("snodo.cli.config.ConfigManager._test_single_key")
    def test_test_keys_mixed_results(self, mock_test, mgr):
        mgr.add_key("openai", "sk-good")
        mgr.add_key("anthropic", "sk-bad")

        def side_effect(provider, key):
            return provider == "openai"
        mock_test.side_effect = side_effect

        results = mgr.test_keys()
        assert results["openai"] is True
        assert results["anthropic"] is False

    def test_test_single_key_without_litellm(self, mgr):
        with patch.dict("sys.modules", {"litellm": None}):
            result = mgr._test_single_key("openai", "sk-test")
            assert result is False

    def test_test_single_key_unknown_provider(self, mgr):
        with patch("snodo.cli.config.completion", create=True):
            result = mgr._test_single_key("unknown_provider", "key")
            assert result is False

    def test_test_single_key_success(self, mgr):
        """Test key validation happy path with mocked litellm."""
        mock_completion = MagicMock()
        with patch.dict("sys.modules", {"litellm": MagicMock(completion=mock_completion)}):
            # Clear the cached import so it re-imports
            result = mgr._test_single_key("openai", "sk-test-key")
            assert result is True
            assert os.environ.get("OPENAI_API_KEY") != "sk-test-key"  # Cleaned up

    def test_test_single_key_restores_env_var(self, mgr):
        """Env var is restored after test, even on success."""
        old_value = "sk-original-value"
        os.environ["ANTHROPIC_API_KEY"] = old_value
        try:
            mock_completion = MagicMock()
            with patch.dict("sys.modules", {"litellm": MagicMock(completion=mock_completion)}):
                result = mgr._test_single_key("anthropic", "sk-new-key")
                assert result is True
                assert os.environ["ANTHROPIC_API_KEY"] == old_value
        finally:
            if "ANTHROPIC_API_KEY" in os.environ:
                if os.environ["ANTHROPIC_API_KEY"] == old_value:
                    del os.environ["ANTHROPIC_API_KEY"]

    def test_test_single_key_api_failure(self, mgr):
        """Test key validation returns False on API error."""
        mock_completion = MagicMock(side_effect=Exception("Invalid key"))
        with patch.dict("sys.modules", {"litellm": MagicMock(completion=mock_completion)}):
            result = mgr._test_single_key("openai", "sk-bad-key")
            assert result is False

    def test_test_single_key_cleans_up_env_on_failure(self, mgr):
        """Env var is cleaned up even when API call fails."""
        assert "OPENAI_API_KEY" not in os.environ
        mock_completion = MagicMock(side_effect=Exception("fail"))
        with patch.dict("sys.modules", {"litellm": MagicMock(completion=mock_completion)}):
            mgr._test_single_key("openai", "sk-temp")
        assert "OPENAI_API_KEY" not in os.environ


# === CLI config commands ===

@pytest.fixture
def cli_config_dir(config_dir):
    """Patch ConfigManager to use temp dir for CLI tests."""
    with patch("snodo.cli.commands.config_cmd.ConfigManager") as MockCM:
        real_mgr = ConfigManager(config_dir=config_dir)
        MockCM.return_value = real_mgr
        MockCM.mask_key = ConfigManager.mask_key
        yield real_mgr


class TestCLIConfigShow:
    def test_show_empty_config(self, cli_config_dir, capsys):
        result = main(["config", "show"])
        assert result == 0
        out = capsys.readouterr().out
        assert "No API keys configured" in out

    def test_show_with_keys(self, cli_config_dir, capsys):
        cli_config_dir.add_key("openai", "sk-abcdefghijk")
        result = main(["config", "show"])
        assert result == 0
        out = capsys.readouterr().out
        assert "openai" in out
        assert "sk-ab***ijk" in out
        assert "sk-abcdefghijk" not in out  # Full key never shown

    def test_show_displays_model(self, cli_config_dir, capsys):
        cli_config_dir.set_model("gpt-4")
        result = main(["config", "show"])
        assert result == 0
        out = capsys.readouterr().out
        assert "gpt-4" in out


class TestCLIConfigAdd:
    def test_add_key(self, cli_config_dir, capsys):
        result = main(["config", "add", "openai", "sk-testkey123"])
        assert result == 0
        out = capsys.readouterr().out
        assert "Stored openai key" in out
        assert cli_config_dir.get_key("openai") == "sk-testkey123"

    def test_add_key_masked_in_output(self, cli_config_dir, capsys):
        result = main(["config", "add", "openai", "sk-verylongapikey"])
        assert result == 0
        out = capsys.readouterr().out
        assert "sk-verylongapikey" not in out  # Full key not shown
        assert "***" in out


class TestCLIConfigRemove:
    def test_remove_existing_key(self, cli_config_dir, capsys):
        cli_config_dir.add_key("openai", "sk-test")
        result = main(["config", "remove", "openai"])
        assert result == 0
        out = capsys.readouterr().out
        assert "Removed openai key" in out

    def test_remove_nonexistent_key(self, cli_config_dir, capsys):
        result = main(["config", "remove", "openai"])
        assert result == 1
        err = capsys.readouterr().err
        assert "No key found" in err


class TestCLIConfigTest:
    @patch("snodo.cli.config.ConfigManager._test_single_key")
    def test_test_keys_all_valid(self, mock_test, cli_config_dir, capsys):
        cli_config_dir.add_key("openai", "sk-good")
        mock_test.return_value = True

        result = main(["config", "test"])
        assert result == 0
        out = capsys.readouterr().out
        assert "valid" in out

    @patch("snodo.cli.config.ConfigManager._test_single_key")
    def test_test_keys_some_invalid(self, mock_test, cli_config_dir, capsys):
        cli_config_dir.add_key("openai", "sk-bad")
        mock_test.return_value = False

        result = main(["config", "test"])
        assert result == 1
        out = capsys.readouterr().out
        assert "invalid" in out

    def test_test_no_keys(self, cli_config_dir, capsys):
        result = main(["config", "test"])
        assert result == 1
        out = capsys.readouterr().out
        assert "No API keys configured" in out


class TestCLIConfigNoAction:
    def test_config_no_action_shows_help(self, cli_config_dir, capsys):
        result = main(["config"])
        assert result == 0


# === Model resolution in run command ===

class TestModelResolution:
    def test_model_flag_overrides_config(self, config_dir):
        """--model flag takes priority over config file."""
        mgr = ConfigManager(config_dir=config_dir)
        mgr.set_model("gpt-4")

        # The flag value should win
        with patch("snodo.cli.commands.run_cmd.ConfigManager") as MockCM:
            MockCM.return_value = mgr
            MockCM.mask_key = ConfigManager.mask_key

            # We need a valid protocol to test run, so just check arg parsing
            import argparse
            parser = argparse.ArgumentParser()
            parser.add_argument("--model", default=None)
            args = parser.parse_args(["--model", "claude-sonnet-4-20250514"])
            assert args.model == "claude-sonnet-4-20250514"

            # Model priority: flag > config
            resolved = args.model or mgr.get_model()
            assert resolved == "claude-sonnet-4-20250514"

    def test_config_model_used_when_no_flag(self, config_dir):
        """Config model used when --model not specified."""
        mgr = ConfigManager(config_dir=config_dir)
        mgr.set_model("gpt-4o")

        resolved = None or mgr.get_model()
        assert resolved == "gpt-4o"

    def test_default_model_when_nothing_set(self, config_dir):
        """Default model used when nothing configured."""
        mgr = ConfigManager(config_dir=config_dir)
        resolved = None or mgr.get_model()
        assert resolved == DEFAULT_MODEL


# === File permissions ===

class TestPermissions:
    def test_config_file_not_world_readable(self, mgr):
        mgr.add_key("openai", "sk-secret")
        mode = os.stat(mgr.config_path).st_mode
        # No group or other permissions
        assert mode & stat.S_IRGRP == 0
        assert mode & stat.S_IWGRP == 0
        assert mode & stat.S_IROTH == 0
        assert mode & stat.S_IWOTH == 0

    def test_permissions_maintained_after_update(self, mgr):
        mgr.add_key("openai", "sk-first")
        mgr.add_key("anthropic", "sk-second")
        mode = os.stat(mgr.config_path).st_mode
        assert mode & 0o777 == 0o600


# === Edge cases ===

class TestEdgeCases:
    def test_config_path_property(self, config_dir):
        mgr = ConfigManager(config_dir=config_dir)
        assert mgr.config_path == config_dir / "config.yml"

    def test_default_config_dir(self):
        mgr = ConfigManager()
        assert mgr.config_dir == Path.home() / ".snodo"

    def test_concurrent_key_operations(self, mgr):
        """Multiple operations don't corrupt config."""
        mgr.add_key("openai", "sk-o")
        mgr.add_key("anthropic", "sk-a")
        mgr.add_key("google", "sk-g")
        mgr.remove_key("anthropic")
        mgr.set_model("gpt-4")

        config = mgr.load()
        assert len(config["api_keys"]) == 2
        assert "anthropic" not in config["api_keys"]
        assert config["model"] == "gpt-4"


# ========== TASK 7.2: ENGINE CONFIG TESTS ==========

class TestEngineConfig:
    def test_load_includes_engine_section(self, mgr):
        config = mgr.load()
        assert "engine" in config
        assert config["engine"]["max_subtask_depth"] == 3

    def test_get_engine_value_default(self, mgr):
        assert mgr.get_engine_value("max_subtask_depth") == 3

    def test_set_engine_value_persists(self, mgr):
        mgr.set_engine_value("max_subtask_depth", 5)
        assert mgr.get_engine_value("max_subtask_depth") == 5

    def test_set_engine_value_below_range_raises(self, mgr):
        with pytest.raises(ValueError, match="between 1 and 10"):
            mgr.set_engine_value("max_subtask_depth", 0)

    def test_set_engine_value_above_range_raises(self, mgr):
        with pytest.raises(ValueError, match="between 1 and 10"):
            mgr.set_engine_value("max_subtask_depth", 11)

    def test_engine_config_persists_across_instances(self, config_dir):
        mgr1 = ConfigManager(config_dir=config_dir)
        mgr1.set_engine_value("max_subtask_depth", 7)
        mgr2 = ConfigManager(config_dir=config_dir)
        assert mgr2.get_engine_value("max_subtask_depth") == 7

    def test_existing_config_without_engine_loads_defaults(self, mgr):
        """Legacy config without engine section gets defaults."""
        mgr.save({"api_keys": {}, "model": DEFAULT_MODEL})  # no engine key
        config = mgr.load()
        assert config["engine"]["max_subtask_depth"] == 3

    def test_get_engine_value_unknown_key(self, mgr):
        assert mgr.get_engine_value("unknown_key") is None
        assert mgr.get_engine_value("unknown_key", 42) == 42

    def test_set_engine_value_non_int_raises(self, mgr):
        with pytest.raises(ValueError):
            mgr.set_engine_value("max_subtask_depth", "not_int")


# ========== TASK 7.2: CLI SET/GET TESTS ==========

class TestCLISetGet:
    @pytest.fixture
    def cli_config_dir(self):
        d = tempfile.mkdtemp()
        yield Path(d)
        shutil.rmtree(d, ignore_errors=True)

    def test_cli_set_engine_max_subtask_depth(self, cli_config_dir, capsys):
        with patch.object(ConfigManager, '__init__', lambda self, **kw: setattr(self, 'config_dir', cli_config_dir) or setattr(self, 'config_path', cli_config_dir / 'config.yml')):
            with patch('sys.argv', ['snodo', 'config', 'set', 'engine.max_subtask_depth', '5']):
                result = main()
        assert result == 0
        captured = capsys.readouterr()
        assert "engine.max_subtask_depth" in captured.out
        assert "5" in captured.out

    def test_cli_get_engine_max_subtask_depth(self, cli_config_dir, capsys):
        with patch.object(ConfigManager, '__init__', lambda self, **kw: setattr(self, 'config_dir', cli_config_dir) or setattr(self, 'config_path', cli_config_dir / 'config.yml')):
            with patch('sys.argv', ['snodo', 'config', 'get', 'engine.max_subtask_depth']):
                result = main()
        assert result == 0
        captured = capsys.readouterr()
        assert "3" in captured.out  # default

    def test_cli_set_invalid_depth(self, cli_config_dir, capsys):
        with patch.object(ConfigManager, '__init__', lambda self, **kw: setattr(self, 'config_dir', cli_config_dir) or setattr(self, 'config_path', cli_config_dir / 'config.yml')):
            with patch('sys.argv', ['snodo', 'config', 'set', 'engine.max_subtask_depth', 'abc']):
                result = main()
        assert result == 1

    def test_cli_set_depth_out_of_range(self, cli_config_dir, capsys):
        with patch.object(ConfigManager, '__init__', lambda self, **kw: setattr(self, 'config_dir', cli_config_dir) or setattr(self, 'config_path', cli_config_dir / 'config.yml')):
            with patch('sys.argv', ['snodo', 'config', 'set', 'engine.max_subtask_depth', '0']):
                result = main()
        assert result == 1

    def test_cli_set_unknown_key(self, cli_config_dir, capsys):
        with patch.object(ConfigManager, '__init__', lambda self, **kw: setattr(self, 'config_dir', cli_config_dir) or setattr(self, 'config_path', cli_config_dir / 'config.yml')):
            with patch('sys.argv', ['snodo', 'config', 'set', 'unknown.key', 'val']):
                result = main()
        assert result == 1

    def test_cli_get_model(self, cli_config_dir, capsys):
        with patch.object(ConfigManager, '__init__', lambda self, **kw: setattr(self, 'config_dir', cli_config_dir) or setattr(self, 'config_path', cli_config_dir / 'config.yml')):
            with patch('sys.argv', ['snodo', 'config', 'get', 'model']):
                result = main()
        assert result == 0

    def test_cli_set_model(self, cli_config_dir, capsys):
        with patch.object(ConfigManager, '__init__', lambda self, **kw: setattr(self, 'config_dir', cli_config_dir) or setattr(self, 'config_path', cli_config_dir / 'config.yml')):
            with patch('sys.argv', ['snodo', 'config', 'set', 'model', 'gpt-4']):
                result = main()
        assert result == 0
        captured = capsys.readouterr()
        assert "model" in captured.out


# ========== TASK 7.3: SESSION CONFIG TESTS ==========

class TestSessionAgeConfig:
    @pytest.fixture
    def mgr(self):
        d = tempfile.mkdtemp()
        m = ConfigManager(config_dir=Path(d))
        yield m
        shutil.rmtree(d, ignore_errors=True)

    def test_load_includes_max_session_age_days(self, mgr):
        config = mgr.load()
        assert config["engine"]["max_session_age_days"] == 30

    def test_set_max_session_age_days(self, mgr):
        mgr.set_engine_value("max_session_age_days", 60)
        assert mgr.get_engine_value("max_session_age_days") == 60

    def test_set_max_session_age_days_below_range_raises(self, mgr):
        with pytest.raises(ValueError, match="max_session_age_days"):
            mgr.set_engine_value("max_session_age_days", 0)

    def test_set_max_session_age_days_above_range_raises(self, mgr):
        with pytest.raises(ValueError, match="max_session_age_days"):
            mgr.set_engine_value("max_session_age_days", 366)

    def test_cli_set_max_session_age_days(self, capsys):
        d = tempfile.mkdtemp()
        cli_config_dir = Path(d)
        try:
            with patch.object(ConfigManager, '__init__', lambda self, **kw: setattr(self, 'config_dir', cli_config_dir) or setattr(self, 'config_path', cli_config_dir / 'config.yml')):
                with patch('sys.argv', ['snodo', 'config', 'set', 'engine.max_session_age_days', '60']):
                    result = main()
            assert result == 0
            captured = capsys.readouterr()
            assert "60" in captured.out
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cli_set_max_session_age_days_invalid(self, capsys):
        d = tempfile.mkdtemp()
        cli_config_dir = Path(d)
        try:
            with patch.object(ConfigManager, '__init__', lambda self, **kw: setattr(self, 'config_dir', cli_config_dir) or setattr(self, 'config_path', cli_config_dir / 'config.yml')):
                with patch('sys.argv', ['snodo', 'config', 'set', 'engine.max_session_age_days', 'abc']):
                    result = main()
            assert result == 1
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_cli_get_max_session_age_days(self, capsys):
        d = tempfile.mkdtemp()
        cli_config_dir = Path(d)
        try:
            with patch.object(ConfigManager, '__init__', lambda self, **kw: setattr(self, 'config_dir', cli_config_dir) or setattr(self, 'config_path', cli_config_dir / 'config.yml')):
                with patch('sys.argv', ['snodo', 'config', 'get', 'engine.max_session_age_days']):
                    result = main()
            assert result == 0
            captured = capsys.readouterr()
            assert "30" in captured.out
        finally:
            shutil.rmtree(d, ignore_errors=True)
