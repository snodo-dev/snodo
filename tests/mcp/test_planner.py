"""Tests for Planner MCP server (Task 4.2).

FILE: tests/mcp/test_planner.py

Tests cover:
- PlannerMCP initialization
- decompose: create plan structure
- generate_spec: write task specs
- validate_plan: completeness checks
- get_plan, list_plans, get_status, update_status
- Server integration (TOOL_REGISTRY, MODE_TOOL_MAP)
- WF1 enforcement
- Default protocol planner mode
- CLI plan command (list, status)
- CLI run --plan execution
"""

import json
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import yaml

from snodo.mcp.planner import PlannerMCP, PlannerError


# === Fixtures ===

@pytest.fixture
def temp_dir():
    """Create a temporary directory with .snodo/ structure."""
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / ".snodo").mkdir()
        yield tmpdir


@pytest.fixture
def planner(temp_dir):
    """Create a PlannerMCP instance."""
    return PlannerMCP(temp_dir)


@pytest.fixture
def plan_with_tasks(planner):
    """Create a plan with tasks already generated."""
    planner.decompose("Build auth system", "auth")
    planner.generate_spec("auth", "1.1_models", "# Task 1.1: Models\nCreate user model.")
    planner.generate_spec("auth", "1.2_routes", "# Task 1.2: Routes\nCreate auth routes.")
    planner.generate_spec("auth", "2.1_tests", "# Task 2.1: Tests\nWrite integration tests.")
    return planner


# === Initialization ===

class TestPlannerMCPInit:
    def test_init_with_valid_root(self, temp_dir):
        mcp = PlannerMCP(temp_dir)
        assert mcp.project_root == Path(temp_dir).resolve()
        assert mcp.plans_dir == Path(temp_dir).resolve() / ".snodo" / "plans"

    def test_init_nonexistent_root_raises(self):
        with pytest.raises(ValueError, match="does not exist"):
            PlannerMCP("/nonexistent/path/xyz123")

    def test_init_file_as_root_raises(self):
        with tempfile.NamedTemporaryFile() as tmpfile:
            with pytest.raises(ValueError, match="not a directory"):
                PlannerMCP(tmpfile.name)


# === Decompose ===

class TestDecompose:
    def test_creates_plan_directory(self, planner):
        planner.decompose("Build feature X", "feature_x")
        plan_dir = planner.plans_dir / "feature_x"
        assert plan_dir.exists()
        assert plan_dir.is_dir()

    def test_creates_plan_yml(self, planner):
        planner.decompose("Build feature X", "feature_x")
        plan_file = planner.plans_dir / "feature_x" / "plan.yml"
        assert plan_file.exists()

        with open(plan_file) as f:
            data = yaml.safe_load(f)
        assert data["name"] == "feature_x"
        assert data["intent"] == "Build feature X"
        assert data["waves"] == []

    def test_creates_status_json(self, planner):
        planner.decompose("Build feature X", "feature_x")
        status_file = planner.plans_dir / "feature_x" / "status.json"
        assert status_file.exists()

        with open(status_file) as f:
            data = json.load(f)
        assert data == {"tasks": {}}

    def test_returns_plan_data(self, planner):
        result = planner.decompose("Build feature X", "feature_x")
        assert result == {
            "name": "feature_x",
            "intent": "Build feature X",
            "waves": [],
        }

    def test_duplicate_plan_raises(self, planner):
        planner.decompose("Intent A", "plan_a")
        with pytest.raises(PlannerError, match="already exists"):
            planner.decompose("Intent B", "plan_a")

    def test_empty_intent_raises(self, planner):
        with pytest.raises(PlannerError, match="Intent cannot be empty"):
            planner.decompose("", "plan_a")

    def test_whitespace_intent_raises(self, planner):
        with pytest.raises(PlannerError, match="Intent cannot be empty"):
            planner.decompose("   ", "plan_a")

    def test_empty_plan_name_raises(self, planner):
        with pytest.raises(PlannerError, match="Plan name cannot be empty"):
            planner.decompose("Intent", "")

    def test_creates_plans_dir_if_missing(self, temp_dir):
        planner = PlannerMCP(temp_dir)
        assert not planner.plans_dir.exists()
        planner.decompose("Intent", "my_plan")
        assert planner.plans_dir.exists()


# === Generate Spec ===

class TestGenerateSpec:
    def test_creates_spec_file(self, planner):
        planner.decompose("Intent", "plan_a")
        path = planner.generate_spec("plan_a", "1.1_models", "# Models spec")
        spec_file = planner.project_root / path
        assert spec_file.exists()
        assert spec_file.read_text() == "# Models spec"

    def test_creates_wave_directory(self, planner):
        planner.decompose("Intent", "plan_a")
        planner.generate_spec("plan_a", "1.1_models", "spec content")
        wave_dir = planner.plans_dir / "plan_a" / "wave_1"
        assert wave_dir.exists()

    def test_updates_plan_yml_with_task(self, planner):
        planner.decompose("Intent", "plan_a")
        planner.generate_spec("plan_a", "1.1_models", "spec content")

        with open(planner.plans_dir / "plan_a" / "plan.yml") as f:
            data = yaml.safe_load(f)
        assert len(data["waves"]) == 1
        assert data["waves"][0]["id"] == 1
        assert "1.1_models" in data["waves"][0]["tasks"]

    def test_updates_status_json(self, planner):
        planner.decompose("Intent", "plan_a")
        planner.generate_spec("plan_a", "1.1_models", "spec content")

        with open(planner.plans_dir / "plan_a" / "status.json") as f:
            data = json.load(f)
        entry = data["tasks"]["1.1_models"]
        assert isinstance(entry, dict)
        assert entry["status"] == "pending"
        assert entry["depth"] == 0
        assert entry["parent_task_ref"] is None
        assert entry["spec_hash"] is not None

    def test_multiple_tasks_same_wave(self, planner):
        planner.decompose("Intent", "plan_a")
        planner.generate_spec("plan_a", "1.1_models", "spec 1")
        planner.generate_spec("plan_a", "1.2_routes", "spec 2")

        with open(planner.plans_dir / "plan_a" / "plan.yml") as f:
            data = yaml.safe_load(f)
        assert len(data["waves"]) == 1
        assert len(data["waves"][0]["tasks"]) == 2

    def test_tasks_in_different_waves(self, planner):
        planner.decompose("Intent", "plan_a")
        planner.generate_spec("plan_a", "1.1_models", "spec 1")
        planner.generate_spec("plan_a", "2.1_tests", "spec 2")

        with open(planner.plans_dir / "plan_a" / "plan.yml") as f:
            data = yaml.safe_load(f)
        assert len(data["waves"]) == 2
        assert data["waves"][0]["id"] == 1
        assert data["waves"][1]["id"] == 2

    def test_waves_sorted_by_id(self, planner):
        planner.decompose("Intent", "plan_a")
        # Add wave 3 before wave 2
        planner.generate_spec("plan_a", "3.1_deploy", "spec 3")
        planner.generate_spec("plan_a", "2.1_tests", "spec 2")

        with open(planner.plans_dir / "plan_a" / "plan.yml") as f:
            data = yaml.safe_load(f)
        ids = [w["id"] for w in data["waves"]]
        assert ids == [2, 3]

    def test_duplicate_task_id_not_added_twice(self, planner):
        planner.decompose("Intent", "plan_a")
        planner.generate_spec("plan_a", "1.1_models", "spec v1")
        planner.generate_spec("plan_a", "1.1_models", "spec v2", replace=True)

        with open(planner.plans_dir / "plan_a" / "plan.yml") as f:
            data = yaml.safe_load(f)
        assert data["waves"][0]["tasks"].count("1.1_models") == 1

    def test_plan_not_found_raises(self, planner):
        with pytest.raises(PlannerError, match="Plan not found"):
            planner.generate_spec("nonexistent", "1.1_x", "spec")

    def test_empty_task_id_raises(self, planner):
        planner.decompose("Intent", "plan_a")
        with pytest.raises(PlannerError, match="Task ID cannot be empty"):
            planner.generate_spec("plan_a", "", "spec")

    def test_empty_spec_raises(self, planner):
        planner.decompose("Intent", "plan_a")
        with pytest.raises(PlannerError, match="Spec cannot be empty"):
            planner.generate_spec("plan_a", "1.1_x", "")

    def test_invalid_task_id_no_dot_raises(self, planner):
        planner.decompose("Intent", "plan_a")
        with pytest.raises(PlannerError, match="Invalid task_id format"):
            planner.generate_spec("plan_a", "no_dot_id", "spec")

    def test_invalid_wave_number_raises(self, planner):
        planner.decompose("Intent", "plan_a")
        with pytest.raises(PlannerError, match="Invalid wave number"):
            planner.generate_spec("plan_a", "abc.1_x", "spec")

    def test_returns_relative_path(self, planner):
        planner.decompose("Intent", "plan_a")
        path = planner.generate_spec("plan_a", "1.1_models", "spec")
        assert ".snodo/plans/plan_a/wave_1/1.1_models_task.md" in path


# === Validate Plan ===

class TestValidatePlan:
    def test_valid_plan(self, plan_with_tasks):
        result = plan_with_tasks.validate_plan("auth")
        assert result["valid"] is True
        assert result["errors"] == []
        assert result["wave_count"] == 2
        assert result["task_count"] == 3

    def test_plan_not_found_raises(self, planner):
        with pytest.raises(PlannerError, match="Plan not found"):
            planner.validate_plan("nonexistent")

    def test_missing_plan_yml(self, planner):
        plan_dir = planner.plans_dir / "broken"
        plan_dir.mkdir(parents=True)
        result = planner.validate_plan("broken")
        assert result["valid"] is False
        assert "plan.yml not found" in result["errors"]

    def test_missing_intent(self, planner):
        planner.decompose("Intent", "plan_a")
        # Remove intent from plan.yml
        plan_file = planner.plans_dir / "plan_a" / "plan.yml"
        with open(plan_file) as f:
            data = yaml.safe_load(f)
        data["intent"] = ""
        with open(plan_file, "w") as f:
            yaml.dump(data, f)

        result = planner.validate_plan("plan_a")
        assert "Missing intent" in result["errors"]

    def test_no_waves(self, planner):
        planner.decompose("Intent", "plan_a")
        result = planner.validate_plan("plan_a")
        assert "No waves defined" in result["errors"]

    def test_missing_spec_file(self, planner):
        planner.decompose("Intent", "plan_a")
        # Manually add task to plan.yml without creating spec file
        plan_file = planner.plans_dir / "plan_a" / "plan.yml"
        with open(plan_file) as f:
            data = yaml.safe_load(f)
        data["waves"] = [{"id": 1, "tasks": ["1.1_missing"]}]
        with open(plan_file, "w") as f:
            yaml.dump(data, f)

        result = planner.validate_plan("plan_a")
        assert result["valid"] is False
        assert "Missing spec: 1.1_missing" in result["errors"]

    def test_empty_wave_warning(self, planner):
        planner.decompose("Intent", "plan_a")
        plan_file = planner.plans_dir / "plan_a" / "plan.yml"
        with open(plan_file) as f:
            data = yaml.safe_load(f)
        data["waves"] = [{"id": 1, "tasks": []}]
        with open(plan_file, "w") as f:
            yaml.dump(data, f)

        result = planner.validate_plan("plan_a")
        assert "Wave 1 has no tasks" in result["warnings"]

    def test_invalid_dependency_reference(self, planner):
        planner.decompose("Intent", "plan_a")
        planner.generate_spec("plan_a", "2.1_x", "spec")
        # Add depends_on referencing non-existent wave
        plan_file = planner.plans_dir / "plan_a" / "plan.yml"
        with open(plan_file) as f:
            data = yaml.safe_load(f)
        data["waves"][0]["depends_on"] = [99]
        with open(plan_file, "w") as f:
            yaml.dump(data, f)

        result = planner.validate_plan("plan_a")
        assert any("depends on unknown wave 99" in e for e in result["errors"])


# === Get/List/Status ===

class TestPlanManagement:
    def test_get_plan(self, plan_with_tasks):
        data = plan_with_tasks.get_plan("auth")
        assert data["name"] == "auth"
        assert data["intent"] == "Build auth system"
        assert len(data["waves"]) == 2

    def test_get_plan_not_found(self, planner):
        with pytest.raises(PlannerError, match="Plan not found"):
            planner.get_plan("nonexistent")

    def test_list_plans_empty(self, planner):
        assert planner.list_plans() == []

    def test_list_plans_with_plans(self, plan_with_tasks):
        plan_with_tasks.decompose("Intent B", "plan_b")
        plans = plan_with_tasks.list_plans()
        assert len(plans) == 2
        names = {p["name"] for p in plans}
        assert "auth" in names
        assert "plan_b" in names

    def test_list_plans_includes_status_counts(self, plan_with_tasks):
        plan_with_tasks.update_status("auth", "1.1_models", "completed")
        plans = plan_with_tasks.list_plans()
        auth_plan = [p for p in plans if p["name"] == "auth"][0]
        assert auth_plan["status_counts"]["completed"] == 1
        assert auth_plan["status_counts"]["pending"] == 2

    def test_list_plans_sorted(self, planner):
        planner.decompose("Z plan", "z_plan")
        planner.decompose("A plan", "a_plan")
        plans = planner.list_plans()
        assert plans[0]["name"] == "a_plan"
        assert plans[1]["name"] == "z_plan"

    def test_get_status(self, plan_with_tasks):
        status = plan_with_tasks.get_status("auth")
        assert "1.1_models" in status["tasks"]
        entry = status["tasks"]["1.1_models"]
        normalized = plan_with_tasks._normalize_task_entry(entry)
        assert normalized["status"] == "pending"

    def test_get_status_not_found(self, planner):
        with pytest.raises(PlannerError, match="Plan not found"):
            planner.get_status("nonexistent")

    def test_update_status(self, plan_with_tasks):
        plan_with_tasks.update_status("auth", "1.1_models", "in_progress")
        status = plan_with_tasks.get_status("auth")
        entry = plan_with_tasks._normalize_task_entry(status["tasks"]["1.1_models"])
        assert entry["status"] == "in_progress"

    def test_update_status_completed(self, plan_with_tasks):
        plan_with_tasks.update_status("auth", "1.1_models", "completed")
        status = plan_with_tasks.get_status("auth")
        entry = plan_with_tasks._normalize_task_entry(status["tasks"]["1.1_models"])
        assert entry["status"] == "completed"

    def test_update_status_blocked(self, plan_with_tasks):
        plan_with_tasks.update_status("auth", "1.1_models", "blocked")
        status = plan_with_tasks.get_status("auth")
        entry = plan_with_tasks._normalize_task_entry(status["tasks"]["1.1_models"])
        assert entry["status"] == "blocked"

    def test_update_status_invalid_raises(self, plan_with_tasks):
        with pytest.raises(PlannerError, match="Invalid status"):
            plan_with_tasks.update_status("auth", "1.1_models", "unknown")

    def test_update_status_not_found(self, planner):
        with pytest.raises(PlannerError, match="Plan not found"):
            planner.update_status("nonexistent", "1.1_x", "pending")


# === Server Integration ===

class TestServerIntegration:
    def test_planner_tools_in_registry(self):
        from snodo.mcp.server import TOOL_REGISTRY
        for tool in ["decompose", "generate_spec", "validate_plan"]:
            assert tool in TOOL_REGISTRY

    def test_planner_tools_mcp_is_planner(self):
        from snodo.mcp.server import TOOL_REGISTRY
        for tool in ["decompose", "generate_spec", "validate_plan"]:
            assert TOOL_REGISTRY[tool]["mcp"] == "planner"

    def test_decompose_requires_token(self):
        from snodo.mcp.server import TOOL_REGISTRY
        assert TOOL_REGISTRY["decompose"]["requires_token"] is True

    def test_generate_spec_requires_token(self):
        from snodo.mcp.server import TOOL_REGISTRY
        assert TOOL_REGISTRY["generate_spec"]["requires_token"] is True

    def test_validate_plan_no_token(self):
        from snodo.mcp.server import TOOL_REGISTRY
        assert TOOL_REGISTRY["validate_plan"]["requires_token"] is False

    def test_plan_in_mode_tool_map(self):
        from snodo.mcp.server import MODE_TOOL_MAP
        assert "plan" in MODE_TOOL_MAP
        assert set(MODE_TOOL_MAP["plan"]) == {"decompose", "generate_spec", "validate_plan"}


class TestModeFiltering:
    @pytest.fixture
    def project_dir(self):
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                        cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                        cwd=d, capture_output=True, check=True)
        readme = Path(d) / "README.md"
        readme.write_text("test")
        subprocess.run(["git", "add", "."], cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"],
                        cwd=d, capture_output=True, check=True)
        (Path(d) / ".snodo").mkdir()
        yield d
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    @pytest.fixture
    def protocol_with_planner(self):
        from snodo.compiler.models import Protocol
        return Protocol(**{
            "protocol_id": "test",
            "name": "Test Protocol",
            "version": "1.0.0",
            "modes": [
                {
                    "mode_id": "producer",
                    "name": "Producer",
                    "tools": ["edit", "test"],
                    "validators": ["security"],
                },
                {
                    "mode_id": "planner",
                    "name": "Planner",
                    "tools": ["review", "plan"],
                    "validators": ["security"],
                },
            ],
            "validators": [
                {
                    "validator_id": "security",
                    "validator_type": "security",
                    "criteria": ["Check security"],
                },
            ],
            "disagreement_policy": "unanimous",
            "initial_mode": "planner",
        })

    def test_planner_has_plan_tools(self, protocol_with_planner, project_dir):
        from snodo.mcp.server import ProtocolMCPServer
        server = ProtocolMCPServer(protocol_with_planner, project_dir, mode_id="planner")
        tool_names = {t["name"] for t in server.get_tools()}
        assert "decompose" in tool_names
        assert "generate_spec" in tool_names
        assert "validate_plan" in tool_names

    def test_planner_has_review_tools(self, protocol_with_planner, project_dir):
        from snodo.mcp.server import ProtocolMCPServer
        server = ProtocolMCPServer(protocol_with_planner, project_dir, mode_id="planner")
        tool_names = {t["name"] for t in server.get_tools()}
        assert "read_file" in tool_names
        assert "list_files" in tool_names

    def test_planner_cannot_edit(self, protocol_with_planner, project_dir):
        from snodo.mcp.server import ProtocolMCPServer
        server = ProtocolMCPServer(protocol_with_planner, project_dir, mode_id="planner")
        tool_names = {t["name"] for t in server.get_tools()}
        assert "write_file" not in tool_names
        assert "commit" not in tool_names
        assert "stage_files" not in tool_names

    def test_producer_no_plan_tools(self, protocol_with_planner, project_dir):
        from snodo.mcp.server import ProtocolMCPServer
        server = ProtocolMCPServer(protocol_with_planner, project_dir, mode_id="producer")
        tool_names = {t["name"] for t in server.get_tools()}
        assert "decompose" not in tool_names
        assert "generate_spec" not in tool_names
        assert "validate_plan" not in tool_names

    def test_planner_wf1_enforced(self, protocol_with_planner, project_dir):
        from snodo.mcp.server import ProtocolMCPServer, MCPError
        server = ProtocolMCPServer(protocol_with_planner, project_dir, mode_id="planner")

        with pytest.raises(MCPError, match="WF1 violation"):
            server.call_tool("decompose", {"intent": "test", "plan_name": "p"})

    def test_validate_plan_works_without_token(self, protocol_with_planner, project_dir):
        from snodo.mcp.server import ProtocolMCPServer
        server = ProtocolMCPServer(protocol_with_planner, project_dir, mode_id="planner")

        # validate_plan is read-only, no WF1 token needed
        # Plan doesn't exist, but PlannerError is wrapped as MCPError
        from snodo.mcp.server import MCPError
        with pytest.raises(MCPError, match="Plan not found"):
            server.call_tool("validate_plan", {"plan_name": "nonexistent"})


# === Default Protocol ===

class TestDefaultProtocol:
    def test_default_protocol_has_planner_mode(self):
        from snodo.cli.main import DEFAULT_PROTOCOL
        data = yaml.safe_load(DEFAULT_PROTOCOL)
        modes = {m["mode_id"] for m in data["modes"]}
        assert "planner" in modes

    def test_planner_mode_has_assess_and_plan_tools(self):
        from snodo.cli.main import DEFAULT_PROTOCOL
        data = yaml.safe_load(DEFAULT_PROTOCOL)
        planner = [m for m in data["modes"] if m["mode_id"] == "planner"][0]
        assert "assess" in planner["tools"]
        assert "plan" in planner["tools"]

    def test_planner_mode_no_edit_tools(self):
        from snodo.cli.main import DEFAULT_PROTOCOL
        data = yaml.safe_load(DEFAULT_PROTOCOL)
        planner = [m for m in data["modes"] if m["mode_id"] == "planner"][0]
        assert "edit" not in planner["tools"]
        assert "approve" not in planner["tools"]
        assert "merge" not in planner["tools"]

    def test_planner_has_5_validators(self):
        from snodo.cli.main import DEFAULT_PROTOCOL
        data = yaml.safe_load(DEFAULT_PROTOCOL)
        planner = [m for m in data["modes"] if m["mode_id"] == "planner"][0]
        assert len(planner["validators"]) == 5
        expected = {"intent_clarity", "intent_scope", "agile_conformance",
                    "value_increment", "completeness"}
        assert set(planner["validators"]) == expected

    def test_planning_validators_defined(self):
        from snodo.cli.main import DEFAULT_PROTOCOL
        data = yaml.safe_load(DEFAULT_PROTOCOL)
        validator_ids = {v["validator_id"] for v in data["validators"]}
        expected = {"intent_clarity", "intent_scope", "agile_conformance",
                    "value_increment", "completeness"}
        assert expected.issubset(validator_ids)

    def test_planning_validators_phases(self):
        from snodo.cli.main import DEFAULT_PROTOCOL
        data = yaml.safe_load(DEFAULT_PROTOCOL)
        validators = {v["validator_id"]: v for v in data["validators"]}

        assert validators["intent_clarity"]["evaluation_phase"] == "pre_execute"
        assert validators["intent_scope"]["evaluation_phase"] == "pre_execute"
        assert validators["agile_conformance"]["evaluation_phase"] == "pre_execute"
        assert validators["value_increment"]["evaluation_phase"] == "pre_execute"
        assert validators["completeness"]["evaluation_phase"] == "pre_execute"

    def test_default_protocol_loads_successfully(self):
        from snodo.cli.main import DEFAULT_PROTOCOL, load_protocol
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write(DEFAULT_PROTOCOL)
            f.flush()
            protocol = load_protocol(Path(f.name))
        assert protocol is not None
        assert len(protocol.modes) == 3
        assert len(protocol.validators) == 10

    def test_producer_no_plan_tools_in_default(self):
        from snodo.cli.main import DEFAULT_PROTOCOL
        data = yaml.safe_load(DEFAULT_PROTOCOL)
        producer = [m for m in data["modes"] if m["mode_id"] == "producer"][0]
        assert "plan" not in producer["tools"]


# === CLI Plan Command ===

class TestCLIPlanCommand:
    @pytest.fixture
    def project_with_plan(self):
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                        cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Test"],
                        cwd=d, capture_output=True, check=True)
        readme = Path(d) / "README.md"
        readme.write_text("test")
        subprocess.run(["git", "add", "."], cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"],
                        cwd=d, capture_output=True, check=True)

        (Path(d) / ".snodo").mkdir()
        planner = PlannerMCP(d)
        planner.decompose("Build auth", "auth")
        planner.generate_spec("auth", "1.1_models", "# Models")
        planner.generate_spec("auth", "1.2_routes", "# Routes")

        import os
        original = os.getcwd()
        os.chdir(d)
        yield d
        os.chdir(original)
        import shutil
        shutil.rmtree(d, ignore_errors=True)

    def test_plan_list_empty(self):
        d = tempfile.mkdtemp()
        (Path(d) / ".snodo").mkdir()
        import os
        original = os.getcwd()
        os.chdir(d)
        try:
            from snodo.cli.main import main
            with patch('sys.argv', ['snodo', 'plan', 'list']):
                result = main()
            assert result == 0
        finally:
            os.chdir(original)
            import shutil
            shutil.rmtree(d, ignore_errors=True)

    def test_plan_list_with_plans(self, project_with_plan, capsys):
        from snodo.cli.main import main
        with patch('sys.argv', ['snodo', 'plan', 'list']):
            result = main()
        assert result == 0
        captured = capsys.readouterr()
        assert "auth" in captured.out

    def test_plan_status(self, project_with_plan, capsys):
        from snodo.cli.main import main
        with patch('sys.argv', ['snodo', 'plan', 'status', 'auth']):
            result = main()
        assert result == 0
        captured = capsys.readouterr()
        assert "auth" in captured.out
        assert "1.1_models" in captured.out
        assert "pending" in captured.out

    def test_plan_status_not_found(self, project_with_plan):
        from snodo.cli.main import main
        with patch('sys.argv', ['snodo', 'plan', 'status', 'nonexistent']):
            result = main()
        assert result == 1


# === Edge Cases ===

class TestEdgeCases:
    def test_get_plan_dir_exists_but_no_plan_yml(self, planner):
        """Cover line 267: plan dir exists but plan.yml missing."""
        plan_dir = planner.plans_dir / "broken"
        plan_dir.mkdir(parents=True)
        with pytest.raises(PlannerError, match="plan.yml not found"):
            planner.get_plan("broken")

    def test_list_plans_skips_non_dirs(self, planner):
        """Cover line 284: non-directory entry in plans dir."""
        planner.plans_dir.mkdir(parents=True)
        (planner.plans_dir / "not_a_dir.txt").write_text("junk")
        planner.decompose("Intent", "real_plan")
        plans = planner.list_plans()
        assert len(plans) == 1
        assert plans[0]["name"] == "real_plan"

    def test_list_plans_skips_dir_without_plan_yml(self, planner):
        """Cover line 288: plan dir without plan.yml."""
        planner.plans_dir.mkdir(parents=True)
        (planner.plans_dir / "incomplete").mkdir()
        planner.decompose("Intent", "valid")
        plans = planner.list_plans()
        assert len(plans) == 1
        assert plans[0]["name"] == "valid"

    def test_get_status_no_status_file(self, planner):
        """Cover line 333: status.json missing."""
        plan_dir = planner.plans_dir / "no_status"
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan.yml").write_text("name: no_status\n")
        status = planner.get_status("no_status")
        assert status == {"tasks": {}}

    def test_update_status_no_status_file(self, planner):
        """Cover line 362: update_status when status.json missing."""
        plan_dir = planner.plans_dir / "no_status"
        plan_dir.mkdir(parents=True)
        planner.update_status("no_status", "1.1_x", "pending")
        status = planner.get_status("no_status")
        # update_status on new key writes string format (legacy)
        assert status["tasks"]["1.1_x"] == "pending"
        # But normalize handles it
        entry = planner._normalize_task_entry(status["tasks"]["1.1_x"])
        assert entry["status"] == "pending"


# ========== TASK 7.2: NORMALIZATION LAYER TESTS ==========

class TestNormalizationLayer:
    def test_normalize_string_entry(self):
        entry = PlannerMCP._normalize_task_entry("pending")
        assert entry == {"status": "pending", "parent_task_ref": None, "depth": 0, "spec_hash": None}

    def test_normalize_dict_entry(self):
        d = {"status": "completed", "parent_task_ref": "1.1_x", "depth": 2, "spec_hash": "abc123"}
        entry = PlannerMCP._normalize_task_entry(d)
        assert entry == d

    def test_normalize_partial_dict(self):
        entry = PlannerMCP._normalize_task_entry({"status": "blocked"})
        assert entry["status"] == "blocked"
        assert entry["parent_task_ref"] is None
        assert entry["depth"] == 0
        assert entry["spec_hash"] is None

    def test_backward_compat_get_status_with_legacy(self, planner):
        """Legacy string-format status.json normalizes correctly."""
        planner.decompose("Intent", "legacy_plan")
        # Write legacy format directly
        status_file = planner.plans_dir / "legacy_plan" / "status.json"
        with open(status_file, "w") as f:
            json.dump({"tasks": {"1.1_x": "completed", "1.2_y": "pending"}}, f)

        status = planner.get_status("legacy_plan")
        for tid, entry in status["tasks"].items():
            normalized = planner._normalize_task_entry(entry)
            assert "status" in normalized
            assert normalized["depth"] == 0

    def test_get_task_status_returns_normalized(self, plan_with_tasks):
        entry = plan_with_tasks._get_task_status("auth", "1.1_models")
        assert entry is not None
        assert entry["status"] == "pending"
        assert "depth" in entry
        assert "parent_task_ref" in entry

    def test_get_task_status_not_found(self, plan_with_tasks):
        assert plan_with_tasks._get_task_status("auth", "nonexistent") is None

    def test_list_plans_status_counts_normalized(self, planner):
        """list_plans uses normalization for status counting."""
        planner.decompose("Intent", "mixed")
        # Write mixed format: one string, one dict
        status_file = planner.plans_dir / "mixed" / "status.json"
        with open(status_file, "w") as f:
            json.dump({"tasks": {
                "1.1_x": "completed",
                "1.2_y": {"status": "pending", "parent_task_ref": None, "depth": 0, "spec_hash": None},
            }}, f)
        # Need a task in plan.yml too
        plan_file = planner.plans_dir / "mixed" / "plan.yml"
        with open(plan_file) as f:
            data = yaml.safe_load(f)
        data["waves"] = [{"id": 1, "tasks": ["1.1_x", "1.2_y"]}]
        with open(plan_file, "w") as f:
            yaml.dump(data, f)

        plans = planner.list_plans()
        mixed_plan = [p for p in plans if p["name"] == "mixed"][0]
        assert mixed_plan["status_counts"]["completed"] == 1
        assert mixed_plan["status_counts"]["pending"] == 1


# ========== TASK 7.2: PARENT TRACKING TESTS ==========

class TestParentTracking:
    def test_child_task_depth_is_parent_plus_one(self, plan_with_tasks):
        plan_with_tasks.generate_spec(
            "auth", "1.3_child", "# Child spec",
            parent_task_ref="1.1_models"
        )
        entry = plan_with_tasks._get_task_status("auth", "1.3_child")
        assert entry["depth"] == 1
        assert entry["parent_task_ref"] == "1.1_models"

    def test_grandchild_depth_is_two(self, plan_with_tasks):
        plan_with_tasks.generate_spec(
            "auth", "1.3_child", "# Child",
            parent_task_ref="1.1_models"
        )
        plan_with_tasks.generate_spec(
            "auth", "1.4_grandchild", "# Grandchild",
            parent_task_ref="1.3_child"
        )
        entry = plan_with_tasks._get_task_status("auth", "1.4_grandchild")
        assert entry["depth"] == 2

    def test_root_task_depth_zero(self, plan_with_tasks):
        entry = plan_with_tasks._get_task_status("auth", "1.1_models")
        assert entry["depth"] == 0
        assert entry["parent_task_ref"] is None

    def test_parent_not_found_raises(self, plan_with_tasks):
        with pytest.raises(PlannerError, match="parent_not_found"):
            plan_with_tasks.generate_spec(
                "auth", "1.5_orphan", "# Orphan",
                parent_task_ref="nonexistent_task"
            )

    def test_parent_cross_plan_raises(self, planner):
        """Parent lookup is plan-scoped."""
        planner.decompose("Intent A", "plan_a")
        planner.generate_spec("plan_a", "1.1_x", "spec a")
        planner.decompose("Intent B", "plan_b")
        with pytest.raises(PlannerError, match="parent_not_found"):
            planner.generate_spec(
                "plan_b", "1.1_y", "spec b",
                parent_task_ref="1.1_x"  # exists in plan_a, not plan_b
            )


# ========== TASK 7.2: DEPTH ENFORCEMENT TESTS ==========

class TestDepthEnforcement:
    def test_depth_exceeded_raises(self, plan_with_tasks):
        """Depth > max_subtask_depth rejected."""
        # Build a chain to depth 3 (default max)
        plan_with_tasks.generate_spec(
            "auth", "1.3_d1", "# D1", parent_task_ref="1.1_models"
        )
        plan_with_tasks.generate_spec(
            "auth", "1.4_d2", "# D2", parent_task_ref="1.3_d1"
        )
        plan_with_tasks.generate_spec(
            "auth", "1.5_d3", "# D3", parent_task_ref="1.4_d2"
        )
        # Depth 4 should fail (max default is 3)
        with pytest.raises(PlannerError, match="max_subtask_depth_exceeded"):
            plan_with_tasks.generate_spec(
                "auth", "1.6_d4", "# D4", parent_task_ref="1.5_d3"
            )

    def test_depth_at_max_is_allowed(self, plan_with_tasks):
        """Depth exactly at max is OK."""
        plan_with_tasks.generate_spec(
            "auth", "1.3_d1", "# D1", parent_task_ref="1.1_models"
        )
        plan_with_tasks.generate_spec(
            "auth", "1.4_d2", "# D2", parent_task_ref="1.3_d1"
        )
        # Depth 3 == max 3, should succeed
        path = plan_with_tasks.generate_spec(
            "auth", "1.5_d3", "# D3", parent_task_ref="1.4_d2"
        )
        assert path is not None


# ========== TASK 7.2: CYCLE DETECTION TESTS ==========

class TestCycleDetection:
    def test_cycle_immediate_raises(self, plan_with_tasks):
        """Child spec == parent spec -> cycle."""
        parent_spec = (plan_with_tasks.plans_dir / "auth" / "wave_1" /
                       "1.1_models_task.md").read_text()
        with pytest.raises(PlannerError, match="cycle_detected"):
            plan_with_tasks.generate_spec(
                "auth", "1.3_clone", parent_spec,
                parent_task_ref="1.1_models"
            )

    def test_cycle_deep_raises(self, plan_with_tasks):
        """Grandchild spec == grandparent spec -> cycle."""
        grandparent_spec = (plan_with_tasks.plans_dir / "auth" / "wave_1" /
                            "1.1_models_task.md").read_text()
        plan_with_tasks.generate_spec(
            "auth", "1.3_mid", "# Unique middle spec",
            parent_task_ref="1.1_models"
        )
        with pytest.raises(PlannerError, match="cycle_detected"):
            plan_with_tasks.generate_spec(
                "auth", "1.4_cycle", grandparent_spec,
                parent_task_ref="1.3_mid"
            )

    def test_sibling_same_spec_allowed(self, plan_with_tasks):
        """Sibling with same spec as another sibling is NOT a cycle."""
        plan_with_tasks.generate_spec(
            "auth", "1.3_a", "# Shared spec content",
            parent_task_ref="1.1_models"
        )
        # Another child of same parent with same spec - no ancestor match
        path = plan_with_tasks.generate_spec(
            "auth", "1.4_b", "# Shared spec content",
            parent_task_ref="1.1_models"
        )
        assert path is not None

    def test_no_cycle_with_whitespace_difference(self, plan_with_tasks):
        """Specs that differ only in whitespace ARE considered equal."""
        parent_spec = (plan_with_tasks.plans_dir / "auth" / "wave_1" /
                       "1.1_models_task.md").read_text()
        with pytest.raises(PlannerError, match="cycle_detected"):
            plan_with_tasks.generate_spec(
                "auth", "1.3_ws", "  " + parent_spec + "  ",
                parent_task_ref="1.1_models"
            )


# ========== TASK 7.2: REPLACE FLAG TESTS ==========

class TestReplaceFlag:
    def test_existing_task_without_replace_raises(self, plan_with_tasks):
        with pytest.raises(PlannerError, match="task_exists"):
            plan_with_tasks.generate_spec("auth", "1.1_models", "new spec")

    def test_existing_task_with_replace_succeeds(self, plan_with_tasks):
        path = plan_with_tasks.generate_spec(
            "auth", "1.1_models", "updated spec v2", replace=True
        )
        assert path is not None
        spec = (plan_with_tasks.plans_dir / "auth" / "wave_1" /
                "1.1_models_task.md").read_text()
        assert spec == "updated spec v2"

    def test_new_task_without_replace_succeeds(self, plan_with_tasks):
        path = plan_with_tasks.generate_spec("auth", "1.3_new", "brand new spec")
        assert path is not None


# ========== TASK 7.2: AUDIT LOG TESTS ==========

class TestPlannerAuditLog:
    @pytest.fixture
    def audit_planner(self, temp_dir):
        from snodo.infrastructure.audit import AuditLog
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".log", delete=False) as f:
            log_path = f.name
        audit = AuditLog(log_path)
        planner = PlannerMCP(temp_dir, audit_log=audit)
        yield planner, audit
        import os
        os.unlink(log_path)

    def test_audit_log_injection(self, audit_planner):
        planner, audit = audit_planner
        assert planner._audit_log is audit

    def test_no_audit_log_no_error(self, temp_dir):
        planner = PlannerMCP(temp_dir, audit_log=None)
        planner.decompose("Intent", "p")
        planner.generate_spec("p", "1.1_x", "spec")

    def test_task_added_event(self, audit_planner):
        planner, audit = audit_planner
        planner.decompose("Intent", "p")
        planner.generate_spec("p", "1.1_x", "spec content")

        events = audit.get_history(event_type="task_added")
        assert len(events) == 1
        assert events[0].data["task_id"] == "1.1_x"
        assert events[0].data["plan_name"] == "p"
        assert events[0].data["depth"] == 0
        assert "spec_hash" in events[0].data

    def test_task_add_rejected_event_task_exists(self, audit_planner):
        planner, audit = audit_planner
        planner.decompose("Intent", "p")
        planner.generate_spec("p", "1.1_x", "spec")
        try:
            planner.generate_spec("p", "1.1_x", "another spec")
        except PlannerError:
            pass
        events = audit.get_history(event_type="task_add_rejected")
        assert len(events) == 1
        assert events[0].data["reason"] == "task_exists"

    def test_task_add_rejected_event_parent_not_found(self, audit_planner):
        planner, audit = audit_planner
        planner.decompose("Intent", "p")
        try:
            planner.generate_spec("p", "1.1_x", "spec", parent_task_ref="ghost")
        except PlannerError:
            pass
        events = audit.get_history(event_type="task_add_rejected")
        assert len(events) == 1
        assert events[0].data["reason"] == "parent_not_found"

    def test_task_replaced_event(self, audit_planner):
        planner, audit = audit_planner
        planner.decompose("Intent", "p")
        planner.generate_spec("p", "1.1_x", "original spec")
        planner.generate_spec("p", "1.1_x", "new spec", replace=True)

        events = audit.get_history(event_type="task_replaced")
        assert len(events) == 1
        assert events[0].data["task_id"] == "1.1_x"
        assert "old_spec_hash" in events[0].data
        assert "new_spec_hash" in events[0].data
        assert events[0].data["old_spec_hash"] != events[0].data["new_spec_hash"]

    def test_task_add_rejected_max_depth(self, audit_planner):
        planner, audit = audit_planner
        planner.decompose("Intent", "p")
        planner.generate_spec("p", "1.1_root", "root")
        planner.generate_spec("p", "1.2_d1", "d1", parent_task_ref="1.1_root")
        planner.generate_spec("p", "1.3_d2", "d2", parent_task_ref="1.2_d1")
        planner.generate_spec("p", "1.4_d3", "d3", parent_task_ref="1.3_d2")
        try:
            planner.generate_spec("p", "1.5_d4", "d4", parent_task_ref="1.4_d3")
        except PlannerError:
            pass
        events = audit.get_history(event_type="task_add_rejected")
        assert any(e.data["reason"] == "max_subtask_depth_exceeded" for e in events)


# ========== TASK 7.2: RECOMPUTE DEPTHS TESTS ==========

class TestRecomputeDepths:
    def test_recompute_legacy_plan(self, planner):
        """Legacy plan with all depth=0 gets correct depths after recompute."""
        planner.decompose("Intent", "legacy")
        # Write legacy-style status with parent refs but depth=0
        status_file = planner.plans_dir / "legacy" / "status.json"
        with open(status_file, "w") as f:
            json.dump({"tasks": {
                "1.1_root": {"status": "completed", "parent_task_ref": None, "depth": 0, "spec_hash": "a"},
                "1.2_child": {"status": "pending", "parent_task_ref": "1.1_root", "depth": 0, "spec_hash": "b"},
                "1.3_grandchild": {"status": "pending", "parent_task_ref": "1.2_child", "depth": 0, "spec_hash": "c"},
            }}, f)

        result = planner.recompute_depths("legacy")
        assert result["1.1_root"] == 0
        assert result["1.2_child"] == 1
        assert result["1.3_grandchild"] == 2

    def test_recompute_updates_status_file(self, planner):
        planner.decompose("Intent", "p")
        status_file = planner.plans_dir / "p" / "status.json"
        with open(status_file, "w") as f:
            json.dump({"tasks": {
                "1.1_root": {"status": "completed", "parent_task_ref": None, "depth": 0, "spec_hash": "a"},
                "1.2_child": {"status": "pending", "parent_task_ref": "1.1_root", "depth": 0, "spec_hash": "b"},
            }}, f)

        planner.recompute_depths("p")

        with open(status_file) as f:
            data = json.load(f)
        assert data["tasks"]["1.2_child"]["depth"] == 1

    def test_recompute_empty_plan(self, planner):
        planner.decompose("Intent", "empty")
        result = planner.recompute_depths("empty")
        assert result == {}

    def test_recompute_plan_not_found(self, planner):
        with pytest.raises(PlannerError, match="Plan not found"):
            planner.recompute_depths("nonexistent")


# ========== TASK 7.2: MCP TOOL_REGISTRY TESTS ==========

class TestToolRegistryUpdate:
    def test_generate_spec_has_parent_task_ref_property(self):
        from snodo.mcp.server import TOOL_REGISTRY
        props = TOOL_REGISTRY["generate_spec"]["inputSchema"]["properties"]
        assert "parent_task_ref" in props
        assert props["parent_task_ref"]["type"] == "string"

    def test_generate_spec_has_replace_property(self):
        from snodo.mcp.server import TOOL_REGISTRY
        props = TOOL_REGISTRY["generate_spec"]["inputSchema"]["properties"]
        assert "replace" in props
        assert props["replace"]["type"] == "boolean"

    def test_parent_task_ref_not_required(self):
        from snodo.mcp.server import TOOL_REGISTRY
        required = TOOL_REGISTRY["generate_spec"]["inputSchema"]["required"]
        assert "parent_task_ref" not in required
        assert "replace" not in required

    def test_server_passes_audit_log_to_planner(self):
        """ProtocolMCPServer passes audit_log to PlannerMCP."""
        import subprocess
        import tempfile
        d = tempfile.mkdtemp()
        subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "t@t.com"],
                       cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "T"],
                       cwd=d, capture_output=True, check=True)
        (Path(d) / "README.md").write_text("x")
        subprocess.run(["git", "add", "."], cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"],
                       cwd=d, capture_output=True, check=True)
        (Path(d) / ".snodo").mkdir()

        from snodo.compiler.models import Protocol
        from snodo.mcp.server import ProtocolMCPServer
        protocol = Protocol(**{
            "protocol_id": "test", "name": "T", "version": "1.0.0",
            "modes": [{"mode_id": "p", "name": "P", "tools": ["plan"],
                       "validators": ["s"]}],
            "validators": [{"validator_id": "s", "validator_type": "security",
                           "criteria": ["x"]}],
            "disagreement_policy": "unanimous", "initial_mode": "p",
        })
        mock_audit = MagicMock()
        server = ProtocolMCPServer(protocol, d, audit_log=mock_audit)
        assert server.planner._audit_log is mock_audit

        import shutil
        shutil.rmtree(d, ignore_errors=True)


# ========== PART A: characterization — 8 missing lines ==========

class TestReadTaskSpecEdgePaths:
    def test_no_dot_returns_none(self, planner):
        """Line 119: task_id with no dot → None."""
        result = planner._read_task_spec("any_plan", "nodot")
        assert result is None

    def test_non_int_wave_returns_none(self, planner):
        """Lines 123-124: wave part can't be int → ValueError → None."""
        result = planner._read_task_spec("any_plan", "abc.1_x")
        assert result is None

    def test_spec_file_missing_returns_none(self, planner):
        """Line 127: valid format, dir exists, but spec file absent → None."""
        planner.decompose("Intent", "p")
        # wave_1 dir doesn't exist yet, so spec_file.exists() is False
        result = planner._read_task_spec("p", "1.1_missing")
        assert result is None


class TestCheckCycleAncestorMissing:
    def test_ancestor_not_in_status_breaks(self, planner):
        """Line 152: ancestor_entry is None → loop breaks cleanly (no error)."""
        planner.decompose("Intent", "p")
        # Write status with a dangling parent reference
        import json
        status_file = planner.plans_dir / "p" / "status.json"
        with open(status_file, "w") as f:
            json.dump({"tasks": {
                "1.1_child": {
                    "status": "pending",
                    "parent_task_ref": "ghost_parent",
                    "depth": 1,
                    "spec_hash": "x",
                },
            }}, f)
        # _check_cycle walks to ghost_parent, finds no entry, breaks — no error
        planner._check_cycle("p", "some new spec", "1.1_child")


class TestDecomposeMkdirFails:
    def test_oserror_raises_planner_error(self, temp_dir):
        """Lines 183-184: OSError during plan_dir.mkdir → PlannerError."""
        from pathlib import Path
        planner = PlannerMCP(temp_dir)
        # Pre-create plans_dir so the OSError comes from the inner mkdir
        planner.plans_dir.mkdir(parents=True, exist_ok=True)
        planner.plans_dir / "p"
        with patch.object(Path, "mkdir", side_effect=OSError("disk full")):
            with pytest.raises(PlannerError, match="Failed to create plan directory"):
                planner.decompose("Intent", "p")


class TestRecomputeDepthsNoStatusFile:
    def test_no_status_json_returns_empty(self, planner):
        """Line 627: plan dir exists but status.json absent → {}."""
        plan_dir = planner.plans_dir / "no_status"
        plan_dir.mkdir(parents=True)
        result = planner.recompute_depths("no_status")
        assert result == {}
