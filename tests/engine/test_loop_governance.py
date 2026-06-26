"""Governance node branch coverage tests.

FILE: tests/engine/test_loop_governance.py
"""

import pytest
from unittest.mock import patch, MagicMock
from snodo.compiler.models import Protocol
from snodo.engine.loop import GraphBuilder, LoopStage, LoopState
from snodo.core.interfaces import Task


@pytest.fixture
def sample_protocol():
    from snodo.compiler.models import Mode, Validator
    return Protocol(
        protocol_id="test_protocol",
        name="Test Protocol",
        version="1.0.0",
        modes=[
            Mode(
                mode_id="producer",
                name="Producer Mode",
                tools=["edit", "test"],
                validators=["security"]
            )
        ],
        validators=[
            Validator(
                validator_id="security",
                validator_type="security",
                criteria=["Check OWASP Top 10"]
            )
        ],
        initial_mode="producer"
    )


@pytest.fixture
def sample_task():
    return Task(
        id="task_001",
        spec="Implement feature X"
    )


def test_governance_max_iterations_exceeded(sample_protocol, sample_task):
    """Test that iteration > 50 blocks the loop with max_iterations halt type."""
    builder = GraphBuilder(sample_protocol)
    
    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 50,
        "stage": "governance",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {}
    }
    
    result = builder._governance_node(initial_state)
    assert result["is_blocked"] is True
    assert result["halt_type"] == "max_iterations"
    assert "Max iterations (50) exceeded" in result["constraint_violations"][0]


def test_governance_wave_classification_failure(sample_protocol, sample_task, capsys):
    """Test wave-classification exception path printing '[WAVE] classification failed'."""
    builder = GraphBuilder(sample_protocol)
    builder._project_root = "/fake/project"
    
    initial_state = {
        "task": {"id": sample_task.id, "spec": sample_task.spec},
        "current_mode": "producer",
        "iteration": 0,
        "stage": "governance",
        "validation_results": [],
        "validation_token": None,
        "artifacts": [],
        "constraints_passed": True,
        "constraint_violations": [],
        "policy_decision": None,
        "is_complete": False,
        "is_blocked": False,
        "metadata": {}
    }
    
    with patch("snodo.infrastructure.wave_registry.WaveRegistry.classify_task", side_effect=Exception("mocked classification error")):
        result = builder._governance_node(initial_state)
        
    captured = capsys.readouterr()
    assert "[WAVE] classification failed for task_001: mocked classification error" in captured.err
    # The flow type and wave_id should remain unchanged (None) on error
    assert result["task"]["flow_type"] is None
    assert result["task"]["wave_id"] is None
