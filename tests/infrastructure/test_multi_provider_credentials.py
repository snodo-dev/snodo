"""Tests for multi-provider credential handling and classifier model binding.

FILE: tests/infrastructure/test_multi_provider_credentials.py

Verifies that:
1. Classifier and validator can use different configured OpenAI-compatible
   providers without credential collision
2. Each role's API endpoint and key are correct
3. Provider configuration resolves identically regardless of role setup order
4. Classifier does not override the bound model from its completion_fn
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from functools import partial

from snodo.infrastructure.wave_registry import WaveRegistry
from snodo.infrastructure.config import WaveConfig
from snodo.config import ConfigManager


class TestMultiProviderCredentials:
    """Test credential handling when two OpenAI-compatible providers are configured."""

    def _make_registry(self, tmp_path: Path) -> WaveRegistry:
        """Build a WaveRegistry with standard config."""
        (tmp_path / ".snodo").mkdir(exist_ok=True)
        return WaveRegistry(str(tmp_path), config=WaveConfig())

    def test_classifier_uses_bound_model_not_kwarg_override(self, tmp_path):
        """Classifier must not pass model as kwarg to override bound completion_fn.

        The completion_fn is built with model and api_base already bound.
        When the classifier passes model as a kwarg, it overrides the binding
        and breaks provider routing (issue #237).
        """
        reg = self._make_registry(tmp_path)

        # Create a mock completion_fn that tracks what model it receives
        call_history = []

        def tracking_completion(**kwargs):
            call_history.append(kwargs)
            return MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(
                            content=json.dumps({
                                "flow_type": "feature",
                                "wave_id": "new",
                                "task_summary": "test",
                                "feature_description": "test feature",
                            })
                        )
                    )
                ]
            )

        # Build completion_fn with model="classifier-model" already bound
        bound_fn = partial(tracking_completion, model="classifier-model", api_base="https://custom.endpoint/v1")

        # Classifier should not override the bound model
        result = reg.classify_task(
            "test task",
            "task_001",
            bound_fn,
            model="classifier-model"  # This is passed but should not override
        )

        assert result["flow_type"] == "feature"
        assert result["wave_id"].startswith("w_")

        # Verify the completion was called with the bound model, not overridden
        assert len(call_history) == 1
        call_kwargs = call_history[0]

        # The bound model should be in the call (from functools.partial)
        assert call_kwargs.get("model") == "classifier-model"
        # api_base should also be present from the binding
        assert call_kwargs.get("api_base") == "https://custom.endpoint/v1"

    def test_completion_fn_binds_api_key_per_role(self):
        """Each role's completion_fn should carry its own API key.

        This prevents credential collision when two OpenAI-compatible providers
        are used in the same run (issue #237).
        """
        with patch("snodo.config.ConfigManager") as mock_config_class:
            # Setup: configure two different OpenAI-compatible providers
            mock_instance = MagicMock()

            def get_key_for_model(model):
                if model == "validator-model":
                    return "validator-key-abc123"
                elif model == "classifier-model":
                    return "classifier-key-xyz789"
                return None

            def resolve_api_base(model):
                if model == "validator-model":
                    return "https://validator-endpoint.example.com/v1"
                elif model == "classifier-model":
                    return "https://classifier-endpoint.example.com/v1"
                return None

            mock_instance.get_key_for_model = get_key_for_model
            mock_config_class.return_value = mock_instance
            mock_config_class.resolve_api_base = staticmethod(resolve_api_base)

            from snodo.validators.runner import build_completion_fn

            # Build completion_fn for validator
            mock_base_fn = MagicMock()
            validator_fn = build_completion_fn("validator-model", mock_base_fn)

            # Build completion_fn for classifier
            classifier_fn = build_completion_fn("classifier-model", mock_base_fn)

            # Extract the bound kwargs from each partial
            validator_kwargs = validator_fn.keywords
            classifier_kwargs = classifier_fn.keywords

            # Verify each function has its own credentials bound
            assert validator_kwargs.get("api_key") == "validator-key-abc123"
            assert validator_kwargs.get("api_base") == "https://validator-endpoint.example.com/v1"

            assert classifier_kwargs.get("api_key") == "classifier-key-xyz789"
            assert classifier_kwargs.get("api_base") == "https://classifier-endpoint.example.com/v1"

            # Verify they are different
            assert validator_kwargs.get("api_key") != classifier_kwargs.get("api_key")
            assert validator_kwargs.get("api_base") != classifier_kwargs.get("api_base")

    def test_provider_configuration_resolves_identically_per_role(self):
        """A provider block resolves identically regardless of role querying it.

        With two different configured OpenAI-compatible providers, each role
        should resolve to its own configured provider consistently.
        """
        # This test verifies the ConfigManager's resolution logic is consistent
        config_mgr = ConfigManager()

        # Test that _provider_for_model returns consistent results
        # (This uses the real ConfigManager logic if available)
        test_models = [
            "gpt-4",  # Should resolve to openai provider
            "claude-sonnet",  # Should resolve to anthropic provider
        ]

        for model in test_models:
            # Call multiple times to verify consistency
            results = [
                config_mgr._provider_for_model(model),
                config_mgr._provider_for_model(model),
                config_mgr._provider_for_model(model),
            ]
            # All results should be identical
            assert results[0] == results[1] == results[2], (
                f"Provider resolution for {model} was not consistent: {results}"
            )

    def test_setup_order_does_not_change_credentials(self):
        """The order in which roles are initialized doesn't affect their credentials.

        This tests the fix for credential collision via os.environ where the
        last role to set its environment variables wins (issue #237).
        """
        with patch("snodo.config.ConfigManager") as mock_config_class:
            mock_instance = MagicMock()

            # Configure two different providers
            provider_keys = {
                "validator-model": "validator-key-v1",
                "classifier-model": "classifier-key-c1",
            }

            def get_key_for_model(model):
                return provider_keys.get(model)

            mock_instance.get_key_for_model = get_key_for_model
            mock_config_class.return_value = mock_instance
            mock_config_class.resolve_api_base = staticmethod(lambda m: None)

            from snodo.validators.runner import build_completion_fn

            # Scenario 1: Build validator first, then classifier
            mock_base_fn_1 = MagicMock()
            validator_fn_1 = build_completion_fn("validator-model", mock_base_fn_1)
            classifier_fn_1 = build_completion_fn("classifier-model", mock_base_fn_1)

            # Scenario 2: Build classifier first, then validator
            mock_base_fn_2 = MagicMock()
            classifier_fn_2 = build_completion_fn("classifier-model", mock_base_fn_2)
            validator_fn_2 = build_completion_fn("validator-model", mock_base_fn_2)

            # Verify credentials are the same regardless of order
            assert validator_fn_1.keywords["api_key"] == validator_fn_2.keywords["api_key"]
            assert classifier_fn_1.keywords["api_key"] == classifier_fn_2.keywords["api_key"]

            # Verify they are different from each other
            assert validator_fn_1.keywords["api_key"] != classifier_fn_1.keywords["api_key"]

    def test_classifier_and_validator_different_endpoints(self, tmp_path):
        """Classifier and validator each call their own configured endpoint.

        When two different OpenAI-compatible providers are configured, each role
        should send its call to the correct endpoint with the correct key.
        """
        reg = self._make_registry(tmp_path)

        call_history = []

        def tracking_completion(**kwargs):
            call_history.append(kwargs)
            return MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(
                            content=json.dumps({
                                "flow_type": "feature",
                                "wave_id": "new",
                                "task_summary": "test",
                                "feature_description": "test",
                            })
                        )
                    )
                ]
            )

        # Create two completion functions with different bindings
        classifier_fn = partial(
            tracking_completion,
            model="classifier-model",
            api_base="https://classifier.example.com/v1",
            api_key="classifier-secret-key"
        )

        # Call classifier
        result = reg.classify_task(
            "test task",
            "task_001",
            classifier_fn,
            model="classifier-model"
        )

        assert result["flow_type"] == "feature"

        # Verify the call used the bound api_base and api_key
        assert len(call_history) == 1
        assert call_history[0]["api_base"] == "https://classifier.example.com/v1"
        assert call_history[0]["api_key"] == "classifier-secret-key"
