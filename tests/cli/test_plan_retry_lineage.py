import json
from types import SimpleNamespace

from snodo.cli.commands.plan_retry import (
    current_plan_spec,
    resolve_retry_lineage,
    spec_hash_of,
    spec_was_replaced,
)


def _write_status(tmp_path, plan, task_id, spec):
    status = tmp_path / ".snodo" / "plans" / plan / "status.json"
    status.parent.mkdir(parents=True, exist_ok=True)
    status.write_text(json.dumps({"tasks": {task_id: {"status": "blocked", "spec_hash": spec_hash_of(spec)}}}))


def _write_spec(tmp_path, plan, task_id, content):
    wave_id = task_id.split(".", 1)[0]
    p = tmp_path / ".snodo" / "plans" / plan / f"wave_{wave_id}" / f"{task_id}_task.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_changed_spec_starts_new_retry_lineage(tmp_path):
    _write_spec(tmp_path, "plan", "1.1_task", "new specification")
    _write_status(tmp_path, "plan", "1.1_task", "new specification")
    planner = SimpleNamespace(plans_dir=tmp_path / ".snodo" / "plans", project_root=tmp_path)

    assert spec_was_replaced(
        planner, "plan", "1.1_task", {"original_spec": "old specification"}
    )


def test_unchanged_spec_keeps_retry_lineage(tmp_path):
    _write_spec(tmp_path, "plan", "1.1_task", "same specification")
    _write_status(tmp_path, "plan", "1.1_task", "same specification")
    planner = SimpleNamespace(plans_dir=tmp_path / ".snodo" / "plans", project_root=tmp_path)

    assert not spec_was_replaced(
        planner, "plan", "1.1_task", {"original_spec": "same specification"}
    )


def test_hand_edit_does_not_reset_retry_lineage(tmp_path):
    # Simulate hand-edit: file on disk differs but status.json hash still old
    _write_spec(tmp_path, "plan", "1.1_task", "hand edited text")
    _write_status(tmp_path, "plan", "1.1_task", "old specification")
    planner = SimpleNamespace(plans_dir=tmp_path / ".snodo" / "plans", project_root=tmp_path)
    assert not spec_was_replaced(
        planner, "plan", "1.1_task", {"original_spec": "old specification"}
    )
    # resolve_retry_lineage also must not reset on hand-edit
    spec, reset = resolve_retry_lineage(str(tmp_path), "plan", "1.1_task", {"original_spec": "old specification"})
    assert not reset
    assert spec == "old specification"


def test_supported_replacement_resets_retry_lineage(tmp_path):
    _write_spec(tmp_path, "plan", "1.1_task", "new via replace")
    _write_status(tmp_path, "plan", "1.1_task", "new via replace")
    planner = SimpleNamespace(plans_dir=tmp_path / ".snodo" / "plans", project_root=tmp_path)
    assert spec_was_replaced(planner, "plan", "1.1_task", {"original_spec": "old specification"})
    spec, reset = resolve_retry_lineage(str(tmp_path), "plan", "1.1_task", {"original_spec": "old specification"})
    assert reset
    assert spec == "new via replace"


def test_absent_spec_file_does_not_reset(tmp_path):
    # No spec file, no status hash → spec_was_replaced False
    _write_status(tmp_path, "plan", "1.1_task", "old specification")
    planner = SimpleNamespace(plans_dir=tmp_path / ".snodo" / "plans", project_root=tmp_path)
    # absent file but status says old spec — not a replacement
    assert not spec_was_replaced(planner, "plan", "1.1_task", {"original_spec": "old specification"})
    spec, reset = resolve_retry_lineage(str(tmp_path), "plan", "1.1_task", {"original_spec": "old specification"})
    assert not reset
    assert spec == "old specification"


def test_absent_status_returns_false(tmp_path):
    _write_spec(tmp_path, "plan", "1.1_task", "new specification")
    planner = SimpleNamespace(plans_dir=tmp_path / ".snodo" / "plans", project_root=tmp_path)
    assert not spec_was_replaced(planner, "plan", "1.1_task", {"original_spec": "old specification"})
    spec, reset = resolve_retry_lineage(str(tmp_path), "plan", "1.1_task", {"original_spec": "old specification"})
    assert not reset


def test_current_plan_spec_absent_returns_empty(tmp_path):
    assert current_plan_spec(str(tmp_path), "plan", "1.1_task") == ""
    spec, reset = resolve_retry_lineage(str(tmp_path), "plan", "1.1_task", {"original_spec": "old specification"})
    assert not reset


def test_fallback_to_spec_key(tmp_path):
    _write_spec(tmp_path, "plan", "1.1_task", "new via replace")
    _write_status(tmp_path, "plan", "1.1_task", "new via replace")
    planner = SimpleNamespace(plans_dir=tmp_path / ".snodo" / "plans", project_root=tmp_path)
    # Older failure used "spec" not "original_spec"
    assert spec_was_replaced(planner, "plan", "1.1_task", {"spec": "old specification"})
    spec, reset = resolve_retry_lineage(str(tmp_path), "plan", "1.1_task", {"spec": "old specification"})
    assert reset
    assert spec == "new via replace"


def test_plan_retry_decision_retry_when_spec_replaced(tmp_path):
    """Exercise _plan_retry_decision returning retry despite exhausted attempts."""
    from snodo.cli.commands.plan_run import _plan_retry_decision
    from unittest.mock import MagicMock

    # Create status with new spec hash
    _write_spec(tmp_path, "plan", "1.1_task", "new spec after replace")
    _write_status(tmp_path, "plan", "1.1_task", "new spec after replace")
    # Build failure that would be exhausted (attempt 3, max_retries 3) but spec changed
    failure = {"original_spec": "old spec", "attempt": 3, "branch": "task/1.1_task"}

    # Mock session plumbing
    planner = SimpleNamespace(project_root=tmp_path, plans_dir=tmp_path / ".snodo" / "plans",
                              get_status=lambda name: {"tasks": {"1.1_task": {"status": "blocked", "spec_hash": spec_hash_of("new spec after replace")}}})
    # Need actual planner hash read via status.json file already set up
    # Mock protocol
    protocol = SimpleNamespace(execution=SimpleNamespace(max_retries=3), initial_mode="producer")
    session = SimpleNamespace(checkpoint=SimpleNamespace(decisions={"task_failure": {"1.1_task": failure}}))
    session_manager = MagicMock()
    session_manager.get_active_session.return_value = session
    args = SimpleNamespace(plan="plan", session_manager=session_manager)

    # Patch read_state to avoid filesystem mode lookup
    from unittest.mock import patch
    with patch("snodo.infrastructure.state.read_state") as mock_state:
        mock_state.return_value = SimpleNamespace(current_mode="producer")
        result = _plan_retry_decision(planner, args, protocol, "1.1_task")
    assert result == "retry"


def test_retry_path_resets_and_rebuilds_spec(tmp_path, monkeypatch):
    """Exercise _retry_task path that resets attempt and rebuilds spec."""
    from snodo.cli.commands.run_cmd import _retry_task
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    project_root = str(tmp_path)
    # Plan spec replaced
    _write_spec(tmp_path, "myplan", "1.1_task", "replaced spec content")
    _write_status(tmp_path, "myplan", "1.1_task", "replaced spec content")
    # Setup session with exhausted failure
    failure = {"original_spec": "old spec content", "attempt": 5, "branch": "task/1.1_task", "spec": "old spec content"}
    session = SimpleNamespace(
        session_id="sid",
        mode="producer",
        project_root=tmp_path,
        checkpoint=SimpleNamespace(decisions={
            "task_failure": {"1.1_task": failure},
            "pending_decisions": {},
            "halt": {},
        }),
    )
    session_manager = MagicMock()
    session_manager.get_active_session.return_value = session

    args = SimpleNamespace(
        plan="myplan",
        protocol=".snodo/protocol.yml",
        model=None, retry="1.1_task", description=None,
        append_spec=None, replace_spec=None, module=None,
        verbose=False, mock=True,
    )
    # Need protocol file - mock load_protocol
    fake_protocol = SimpleNamespace(execution=SimpleNamespace(max_retries=3), name="p", modes=[], validators=[], disagreement_policy=SimpleNamespace(value="x"))
    with patch("snodo.cli.commands.run_cmd.load_protocol", return_value=fake_protocol), \
         patch("snodo.cli.commands.run_cmd._execute_task", return_value=0) as mock_exec, \
         patch("snodo.infrastructure.state.read_state", return_value=SimpleNamespace(current_mode="producer")), \
         patch("snodo.infrastructure.paths.require_project_root", return_value=project_root):
        # Need to mock ConfigManager inside _retry_task's model resolution
        result = _retry_task(args, "1.1_task", project_root, session_manager)
        assert result == 0
        # _execute_task called with task whose spec contains replaced content
        called_task = mock_exec.call_args[0][2]
        assert "replaced spec content" in called_task.spec
