"""Tests for Plan Pydantic models, verify_plan(), and PlanWellFormednessError enforcement.

FILE: tests/mcp/test_plan_verification.py
"""

import json
import pytest
import yaml

from snodo.compiler.models import Plan
from snodo.compiler.verifier import verify_plan, PlanWellFormednessError
from snodo.mcp.planner import PlannerMCP, PlannerError


# ============================================================================
# 1. Canary Gate Test: Hand-crafted malformed plan refused at load
# ============================================================================

def test_canary_malformed_plan_with_dangling_parent_ref_refused(tmp_path):
    """Canary test: A hand-crafted plan with a dangling parent_task_ref must be
    refused at load time, raising PlanWellFormednessError."""
    plan_dir = tmp_path / ".snodo" / "plans" / "malformed_plan"
    plan_dir.mkdir(parents=True)
    wave_dir = plan_dir / "wave_1"
    wave_dir.mkdir()

    # Write plan.yml
    plan_data = {
        "name": "malformed_plan",
        "intent": "Test dangling parent ref",
        "waves": [{"id": 1, "depends_on": [], "tasks": ["1.1"]}],
    }
    (plan_dir / "plan.yml").write_text(yaml.dump(plan_data))

    # Write status.json with dangling parent_task_ref
    status_data = {
        "tasks": {
            "1.1": {
                "status": "pending",
                "parent_task_ref": "nonexistent_task_999",
                "depth": 1,
            }
        }
    }
    (plan_dir / "status.json").write_text(json.dumps(status_data))

    # Write spec file
    (wave_dir / "1.1_task.md").write_text("# Task 1.1\nSpec content")

    planner = PlannerMCP(str(tmp_path))

    # Loading the plan via get_plan MUST raise PlanWellFormednessError
    with pytest.raises(PlanWellFormednessError) as exc_info:
        planner.get_plan("malformed_plan")

    assert "references unknown parent_task_ref 'nonexistent_task_999'" in str(exc_info.value)


# ============================================================================
# 2. Pydantic Model Unit Tests
# ============================================================================

def test_plan_model_instantiation_and_dict_access():
    """Plan model supports attribute and dict-like access for backwards compatibility."""
    plan = Plan.from_dict({
        "name": "auth_plan",
        "intent": "Build auth",
        "waves": [
            {"id": 1, "depends_on": [], "tasks": ["1.1"]}
        ],
    })

    # Attribute access
    assert plan.name == "auth_plan"
    assert plan.intent == "Build auth"
    assert len(plan.waves) == 1
    assert plan.waves[0].id == 1

    # Dict-like access
    assert plan["name"] == "auth_plan"
    assert plan.get("intent") == "Build auth"
    assert plan.get("waves")[0].get("id") == 1
    assert plan.get("nonexistent", "default") == "default"

    # to_dict conversion
    d = plan.to_dict()
    assert d["name"] == "auth_plan"
    assert d["waves"][0]["tasks"] == ["1.1"]


# ============================================================================
# 3. verify_plan Verification Checks
# ============================================================================

def test_verify_plan_parent_ref_cycle():
    """verify_plan detects parent task reference cycles."""
    plan = Plan.from_dict(
        {
            "name": "cycle_plan",
            "intent": "Test cycle",
            "waves": [{"id": 1, "tasks": ["1.1", "1.2"]}],
        },
        {
            "tasks": {
                "1.1": {"status": "pending", "parent_task_ref": "1.2"},
                "1.2": {"status": "pending", "parent_task_ref": "1.1"},
            }
        },
    )

    res = verify_plan(plan)
    assert res.passed is False
    assert any("Parent reference cycle detected" in err for err in res.errors)


def test_verify_plan_wave_dependency_cycle():
    """verify_plan detects wave dependency cycles."""
    plan = Plan.from_dict({
        "name": "wave_cycle",
        "intent": "Test wave cycle",
        "waves": [
            {"id": 1, "depends_on": [2], "tasks": ["1.1"]},
            {"id": 2, "depends_on": [1], "tasks": ["2.1"]},
        ],
    })

    res = verify_plan(plan)
    assert res.passed is False
    assert any("Wave dependency cycle detected" in err for err in res.errors)


def test_verify_plan_wave_number_gaps():
    """verify_plan detects non-contiguous wave numbers."""
    plan = Plan.from_dict({
        "name": "wave_gap",
        "intent": "Test wave gap",
        "waves": [
            {"id": 1, "depends_on": [], "tasks": ["1.1"]},
            {"id": 3, "depends_on": [1], "tasks": ["3.1"]},
        ],
    })

    res = verify_plan(plan)
    assert res.passed is False
    assert any("Wave-number gap detected" in err for err in res.errors)


def test_verify_plan_status_entry_without_matching_task():
    """verify_plan detects status entries that have no matching task in waves."""
    plan = Plan.from_dict(
        {
            "name": "orphan_status",
            "intent": "Test orphan status",
            "waves": [{"id": 1, "tasks": ["1.1"]}],
        },
        {
            "tasks": {
                "1.1": {"status": "pending"},
                "orphan_99": {"status": "pending"},
            }
        },
    )

    res = verify_plan(plan)
    assert res.passed is False
    assert any("Status entry 'orphan_99' has no matching task" in err for err in res.errors)


def test_verify_plan_unknown_wave_dependency():
    """verify_plan detects wave depending on unknown wave ID."""
    plan = Plan.from_dict({
        "name": "unknown_dep",
        "intent": "Test unknown dep",
        "waves": [
            {"id": 1, "depends_on": [99], "tasks": ["1.1"]}
        ],
    })

    res = verify_plan(plan)
    assert res.passed is False
    assert any("depends on unknown wave 99" in err for err in res.errors)


def test_verify_plan_missing_intent_or_waves():
    """verify_plan flags missing intent or empty waves."""
    plan_no_intent = Plan.from_dict({"name": "p1", "intent": "", "waves": [{"id": 1, "tasks": ["1.1"]}]})
    res = verify_plan(plan_no_intent)
    assert res.passed is False
    assert "Missing intent" in res.errors

    plan_no_waves = Plan.from_dict({"name": "p2", "intent": "Some intent", "waves": []})
    res = verify_plan(plan_no_waves)
    assert res.passed is False
    assert "No waves defined" in res.errors


# ============================================================================
# 4. PlannerMCP Integration: validate_plan and get_plan
# ============================================================================

def test_planner_validate_plan_model_verification(tmp_path):
    """validate_plan() validates the Plan model and returns valid=False on errors."""
    plan_dir = tmp_path / ".snodo" / "plans" / "invalid_plan"
    plan_dir.mkdir(parents=True)

    # Missing intent and wave gap (1, 3)
    plan_data = {
        "name": "invalid_plan",
        "intent": "",
        "waves": [
            {"id": 1, "tasks": ["1.1"]},
            {"id": 3, "depends_on": [1], "tasks": ["3.1"]},
        ],
    }
    (plan_dir / "plan.yml").write_text(yaml.dump(plan_data))

    planner = PlannerMCP(str(tmp_path))
    val = planner.validate_plan("invalid_plan")
    assert val["valid"] is False
    assert "Missing intent" in val["errors"]
    assert any("Wave-number gap detected" in err for err in val["errors"])


def test_planner_get_plan_raises_wellformedness_error(tmp_path):
    """get_plan() raises PlanWellFormednessError when loading a malformed plan."""
    plan_dir = tmp_path / ".snodo" / "plans" / "bad_plan"
    plan_dir.mkdir(parents=True)

    plan_data = {
        "name": "bad_plan",
        "intent": "Intent",
        "waves": [{"id": 1, "depends_on": [99], "tasks": ["1.1"]}],
    }
    (plan_dir / "plan.yml").write_text(yaml.dump(plan_data))

    planner = PlannerMCP(str(tmp_path))

    with pytest.raises(PlanWellFormednessError) as exc_info:
        planner.get_plan("bad_plan")

    assert "Wave 1 depends on unknown wave 99" in str(exc_info.value)
    # PlanWellFormednessError inherits from PlannerError
    assert isinstance(exc_info.value, PlannerError)
