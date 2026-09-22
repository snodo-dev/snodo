"""Tests for Plan Pydantic models, verify_plan(), and PlanWellFormednessError enforcement.

FILE: tests/mcp/test_plan_verification.py
"""

import json

import pytest
import yaml
from snodo.compiler.models import Plan
from snodo.compiler.verifier import PlanWellFormednessError, verify_plan
from snodo.compiler.verifier import verify_plan_dir
from snodo.mcp.planner import PlannerError, PlannerMCP

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


def _write_path_validation_plan(tmp_path, cited_path: str, spec_prefix: str = "Update"):
    """Create a plan fixture with an explicit uv workspace member."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.uv.workspace]\nmembers = [\"packages/*\"]\n"
    )
    plan_dir = tmp_path / ".snodo" / "plans" / "path_plan"
    wave_dir = plan_dir / "wave_1"
    wave_dir.mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(yaml.safe_dump({
        "name": "path_plan",
        "intent": "Validate cited paths",
        "waves": [{"id": 1, "depends_on": [], "tasks": ["1.1_paths"]}],
    }))
    (wave_dir / "1.1_paths_task.md").write_text(
        f"# Task\n\n{spec_prefix} `{cited_path}`.\n"
    )
    return plan_dir


def test_verify_plan_resolves_package_relative_spec_path(tmp_path):
    """A path relative to one declared workspace package is not missing."""
    package_file = tmp_path / "packages" / "video" / "consumers" / "video-task.ts"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("export {}\n")
    plan_dir = _write_path_validation_plan(tmp_path, "consumers/video-task.ts")

    result = verify_plan_dir(plan_dir, workspace_root=tmp_path)

    assert result.passed
    assert not any("video-task.ts" in error for error in result.errors)


def test_verify_plan_rejects_spec_path_missing_from_all_workspace_roots(tmp_path):
    """A cited path absent from the root and declared packages invalidates a plan."""
    plan_dir = _write_path_validation_plan(tmp_path, "consumers/nowhere.ts")

    result = verify_plan_dir(plan_dir, workspace_root=tmp_path)

    assert result.passed is False
    assert "Missing referenced path in spec 1.1_paths: consumers/nowhere.ts" in result.errors


def test_verify_plan_warns_when_same_wave_specs_cite_same_file(tmp_path):
    """Same-wave citations name both tasks and the possible shared file."""
    shared_file = tmp_path / "src" / "shared.py"
    shared_file.parent.mkdir()
    shared_file.write_text("VALUE = 1\n")
    plan_dir = tmp_path / ".snodo" / "plans" / "same_wave"
    (plan_dir / "wave_1").mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(yaml.safe_dump({
        "name": "same_wave",
        "intent": "Warn about same-wave overlap",
        "waves": [{"id": 1, "depends_on": [], "tasks": ["1.1_first", "1.2_second"]}],
    }))
    for task_id in ("1.1_first", "1.2_second"):
        (plan_dir / "wave_1" / f"{task_id}_task.md").write_text(
            "Update `src/shared.py`.\n"
        )

    result = verify_plan_dir(plan_dir, workspace_root=tmp_path)

    assert result.passed
    assert len(result.warnings) == 1
    assert "1.1_first" in result.warnings[0]
    assert "1.2_second" in result.warnings[0]
    assert "src/shared.py" in result.warnings[0]


def test_verify_plan_does_not_warn_for_citations_in_different_waves(tmp_path):
    """The same citation in separate waves is not a same-wave overlap."""
    shared_file = tmp_path / "src" / "shared.py"
    shared_file.parent.mkdir()
    shared_file.write_text("VALUE = 1\n")
    plan_dir = tmp_path / ".snodo" / "plans" / "different_waves"
    for wave_id in (1, 2):
        (plan_dir / f"wave_{wave_id}").mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(yaml.safe_dump({
        "name": "different_waves",
        "intent": "Ignore serialized overlap",
        "waves": [
            {"id": 1, "depends_on": [], "tasks": ["1.1_first"]},
            {"id": 2, "depends_on": [1], "tasks": ["2.1_second"]},
        ],
    }))
    (plan_dir / "wave_1" / "1.1_first_task.md").write_text("Update `src/shared.py`.\n")
    (plan_dir / "wave_2" / "2.1_second_task.md").write_text("Update `src/shared.py`.\n")

    result = verify_plan_dir(plan_dir, workspace_root=tmp_path)

    assert result.passed
    assert not any("same-wave file overlap" in warning for warning in result.warnings)


def test_verify_plan_does_not_warn_for_explicitly_non_touching_path(tmp_path):
    shared_file = tmp_path / "src" / "shared.py"
    shared_file.parent.mkdir()
    shared_file.write_text("VALUE = 1\n")
    plan_dir = tmp_path / ".snodo" / "plans" / "non_touching"
    (plan_dir / "wave_1").mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(yaml.safe_dump({
        "name": "non_touching",
        "intent": "Respect task ownership",
        "waves": [{"id": 1, "depends_on": [], "tasks": ["1.1_first", "1.2_second"]}],
    }))
    (plan_dir / "wave_1" / "1.1_first_task.md").write_text(
        "Do not touch `src/shared.py`; a sibling task owns it.\n"
    )
    (plan_dir / "wave_1" / "1.2_second_task.md").write_text(
        "Update `src/shared.py`.\n"
    )

    result = verify_plan_dir(plan_dir, workspace_root=tmp_path)

    assert result.passed
    assert not any("same-wave file overlap" in warning for warning in result.warnings)


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
