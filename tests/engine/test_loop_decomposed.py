"""Tests for the decomposed loop.py methods.

FILE: tests/engine/test_loop_decomposed.py
"""

import pytest
from unittest.mock import MagicMock, patch
from snodo.compiler.models import Protocol, Mode, Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.loop import GraphBuilder
from snodo.infrastructure.config import ConfigLoadError, DEFAULT_MODEL


@pytest.fixture
def sample_protocol():
    return Protocol(
        protocol_id="test_protocol",
        name="Test Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer Mode",
                tools=["edit"],
                validators=["security"]
            )
        ],
        validators=[
            Validator(
                validator_id="security",
                validator_type="security",
                criteria=["Check security"]
            )
        ],
        initial_mode="producer"
    )


@pytest.fixture
def sample_task():
    return Task(id="task_001", spec="Implement feature X")


def test_init_model_precedence(sample_protocol):
    """__init__: assert validator-model precedence:
    llm.validator.model -> llm.validator_llm.model -> model -> DEFAULT_MODEL
    """
    # 1. llm.validator.model
    cfg1 = {"llm": {"validator": {"model": "model-1"}}}
    with patch("snodo.config.ConfigManager.load", return_value=cfg1):
        builder = GraphBuilder(sample_protocol)
        assert builder._validator_runner._default_model == "model-1"

    # 2. llm.validator_llm.model
    cfg2 = {"llm": {"validator_llm": {"model": "model-2"}}}
    with patch("snodo.config.ConfigManager.load", return_value=cfg2):
        builder = GraphBuilder(sample_protocol)
        assert builder._validator_runner._default_model == "model-2"

    # 3. model
    cfg3 = {"model": "model-3"}
    with patch("snodo.config.ConfigManager.load", return_value=cfg3):
        builder = GraphBuilder(sample_protocol)
        assert builder._validator_runner._default_model == "model-3"

    # 4. DEFAULT_MODEL
    cfg4 = {}
    with patch("snodo.config.ConfigManager.load", return_value=cfg4):
        builder = GraphBuilder(sample_protocol)
        assert builder._validator_runner._default_model == DEFAULT_MODEL


def test_init_api_base_set(sample_protocol):
    """__init__: api_base is set when provider has base_url"""
    cfg = {"model": "openai/gpt-4o"}
    provider_mock = MagicMock()
    provider_mock.base_url = "https://custom-api.openai.com/v1"
    
    class MockConfigManager:
        def load(self):
            return cfg
        @staticmethod
        def _provider_for_model(model):
            return "openai"
        @staticmethod
        def resolve_api_base(model):
            return "https://custom-api.openai.com/v1"
            
    class MockProviderManager:
        def get_providers(self):
            return {"openai": provider_mock}
            
    with patch("snodo.config.ConfigManager", MockConfigManager), \
         patch("snodo.config.provider_env", return_value=MagicMock(__enter__=lambda s: MockProviderManager(), __exit__=lambda *a: None)):
        builder = GraphBuilder(sample_protocol)
        # Check if api_base was bound in the partial completion_fn
        func = builder._validator_runner._completion_fn
        assert func.keywords.get("api_base") == "https://custom-api.openai.com/v1"


def test_default_validator_fallback_and_error(sample_protocol, sample_task):
    """_default_validator: config-fallback path & normal path"""
    # 1. Fallback config load error
    builder = GraphBuilder(sample_protocol)
    builder._validator_runner._validator_config = None
    
    with patch("snodo.infrastructure.config.load_llm_config", side_effect=ConfigLoadError("Load failed")):
        results = builder._default_validator(sample_task, sample_protocol.validators, None, "producer")
        assert len(results) == 1
        assert results[0].validator_id == "config"
        assert results[0].severity == "blocker"
        assert "Config error" in results[0].justification

    # 2. Normal path
    builder2 = GraphBuilder(sample_protocol)
    mock_config = MagicMock()
    mock_config.max_tokens = 100
    mock_config.max_tool_turns = 5
    builder2._validator_runner._validator_config = mock_config
    
    mock_dispatch = MagicMock(return_value=ValidatorResult(validator_id="security", severity="pass", justification="OK"))
    builder2._dispatch_one = mock_dispatch
    
    results2 = builder2._default_validator(sample_task, sample_protocol.validators, None, "producer")
    assert len(results2) == 1
    assert results2[0].severity == "pass"


def test_auto_write_halt_payload_scenarios(sample_protocol, sample_task):
    """_auto_write_halt_payload: handles complete, blocked (pre/post), escalated, and no session"""
    builder = GraphBuilder(sample_protocol)
    
    # Mock _merge_into_job_state
    builder._merge_into_job_state = MagicMock()
    
    # Setup state
    loop_state = MagicMock()
    loop_state.task = sample_task
    loop_state.is_complete = True
    loop_state.is_blocked = False
    loop_state.halt_type = None
    loop_state.constraint_violations = []
    loop_state.artifacts = ["art1"]
    loop_state.metadata = {}
    
    # 1. Complete path, no session
    builder._session_manager = None
    builder._auto_write_halt_payload(loop_state)
    builder._merge_into_job_state.assert_called_once()
    payload = builder._merge_into_job_state.call_args[0][0]["halt"]
    assert payload["final_decision"] == "completed"
    assert payload["phase"] == "complete"

    # 2. Blocked path (pre-execute) with session manager mock
    builder._merge_into_job_state.reset_mock()
    loop_state.is_complete = False
    loop_state.is_blocked = True
    loop_state.halt_type = "constraint"
    loop_state.constraint_violations = ["violation 1"]
    loop_state.metadata = {"pre_validation": "dummy_pre"}
    
    session_manager = MagicMock()
    session = MagicMock()
    session.checkpoint.decisions = {}
    session_manager.load_session.return_value = session
    builder._session_manager = session_manager
    builder._session_id = "session_123"
    
    builder._auto_write_halt_payload(loop_state)
    builder._merge_into_job_state.assert_called_once()
    payload = builder._merge_into_job_state.call_args[0][0]["halt"]
    assert payload["final_decision"] == "blocked"
    assert payload["phase"] == "pre_execute"
    assert payload["blocker_reason"] == "violation 1"
    session_manager.update_decision.assert_called_once_with("session_123", "halt", {"task_001": payload})


def test_maybe_respawn_coder_scenarios(sample_protocol):
    """_maybe_respawn_coder: overrides present, absent, or unverified/invalid"""
    builder = GraphBuilder(sample_protocol)
    builder._authorized_decisions = [{"decision": "override"}]
    
    # 1. No decision issuer -> returns early
    builder._decision_issuer = None
    builder._maybe_respawn_coder()
    
    # 2. Overrides present but no coder override
    mock_issuer = MagicMock()
    mock_issuer.find_set_model_overrides.return_value = [{"scope": "validator", "proposed_model": "gpt-4"}]
    builder._decision_issuer = mock_issuer
    builder._maybe_respawn_coder()
    
    # 3. Override present -> respawns coder
    mock_issuer.find_set_model_overrides.return_value = [{"scope": "coder", "proposed_model": "google/gemini-2.5-pro"}]
    
    mock_adapter = MagicMock()
    mock_adapter.model = "google/gemini-2.5-pro"
    
    with patch("snodo.coders.resolve_adapter_class", return_value=MagicMock(return_value=mock_adapter)):
        builder._maybe_respawn_coder()
        assert builder._default_model == "google/gemini-2.5-pro"
        assert builder.coder is mock_adapter
