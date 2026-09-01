"""Behavioral tests for snodo meta CLI command (snodo/cli/commands/meta_cmd.py).

FILE: tests/cli/test_meta_cmd.py
"""

import json
from types import SimpleNamespace

import pytest
import typer

from snodo.cli.commands.meta_cmd import (
    _duration,
    _fmt_cost,
    _fmt_tokens,
    _highlight,
    _meta_job,
    _meta_task,
    _per_role_tokens,
    _summarize_cost,
    _summarize_tokens,
    meta_command,
    register,
)

# ============================================================================
# 1. Registration & Command CLI Entrypoint Tests
# ============================================================================

def test_meta_register():
    """register() registers top-level meta command on typer App."""
    app = typer.Typer()
    register(app)
    # Check registered command names
    command_names = [cmd.name or cmd.callback.__name__ for cmd in app.registered_commands]
    assert "meta" in command_names


def test_meta_command_empty_id(capsys):
    """meta_command returns exit code 1 when composite_id is empty."""
    args = SimpleNamespace(composite_id="")
    res = meta_command(args)
    assert res == 1
    err = capsys.readouterr().err
    assert "Usage: snodo meta" in err


def test_meta_command_not_in_project(monkeypatch):
    """meta_command raises SystemExit(1) when not inside a snodo project."""
    monkeypatch.setattr("snodo.cli.commands.meta_cmd.resolve_project_root", lambda: None)
    args = SimpleNamespace(composite_id="j_12345")
    with pytest.raises(SystemExit) as exc_info:
        meta_command(args)
    assert exc_info.value.code == 1


# ============================================================================
# 2. Job Summary (_meta_job) Tests
# ============================================================================

def test_meta_job_not_found(tmp_path, capsys):
    """_meta_job returns 1 when requested job directory does not exist."""
    res = _meta_job(str(tmp_path), "j_nonexistent")
    assert res == 1
    err = capsys.readouterr().err
    assert "Job not found: j_nonexistent" in err


def test_meta_job_happy_path_with_telemetry(tmp_path, capsys):
    """_meta_job displays job details, tokens, costs, roles, and tool-loop telemetry."""
    job_dir = tmp_path / ".snodo" / "jobs" / "j_testjob123"
    job_dir.mkdir(parents=True)

    state_data = {
        "status": "completed",
        "started_at": 1000.0,
        "completed_at": 1012.5,
        "usage": [
            {
                "role": "coder",
                "prompt_tokens": 1500,
                "completion_tokens": 500,
                "cost": 0.0150,
            },
            {
                "role": "reviewer",
                "prompt_tokens": 800,
                "completion_tokens": 200,
                "cost": 0.0050,
            },
        ],
        "tool_telemetry": [
            {
                "role": "coder",
                "turn_index": 1,
                "tool": "workspace_read",
                "read_hit": False,
                "depth": 0,
            },
            {
                "role": "coder",
                "turn_index": 2,
                "tool": "workspace_read",
                "read_hit": True,
                "depth": 0,
            },
            {
                "role": "coder",
                "turn_index": 3,
                "tool": "submit_files",
                "submit_bytes": 1024,
            },
        ],
        "halt": {
            "final_decision": "completed",
            "artifacts_count": 2,
        },
    }
    (job_dir / "state.json").write_text(json.dumps(state_data))

    task_data = {
        "task_id": "task_abc",
        "description": "Implement feature X for auth system",
        "model": "gpt-4o",
    }
    (job_dir / "task.json").write_text(json.dumps(task_data))

    res = _meta_job(str(tmp_path), "j_testjob123")
    assert res == 0

    out = capsys.readouterr().out
    assert "Job j_testjob123  [completed]  12.5s" in out
    assert "Task: Implement feature X for auth system" in out
    assert "Model: gpt-4o" in out
    assert "Tokens: 3.0k (prompt 2.3k / completion 700)" in out
    assert "Cost: $0.0200" in out
    assert "By role: coder 2.0k | reviewer 1.0k" in out
    assert "Tool-loop telemetry:" in out
    assert "Orientation: 2/3 turns before first submit" in out
    assert "Path miss rate: 1/2 reads were misses" in out
    assert "Re-read by depth: depth 0: 1/2 re-reads" in out
    assert "Submit size: 1 submit(s), median 1024 bytes, max 1024 bytes" in out
    assert "Highlight: completed — 2 artifacts, 3.0k tok, $0.0200" in out


# ============================================================================
# 3. Task Summary (_meta_task) Tests
# ============================================================================

def test_meta_task_no_jobs_dir(tmp_path, capsys):
    """_meta_task returns 1 when no jobs or task state exist."""
    res = _meta_task(str(tmp_path), "task_missing")
    assert res == 1
    err = capsys.readouterr().err
    assert "No jobs found for task task_missing." in err


def test_meta_task_no_matching_jobs(tmp_path, capsys):
    """_meta_task returns 1 when no jobs match task_id and no task state exists."""
    jobs_dir = tmp_path / ".snodo" / "jobs"
    jobs_dir.mkdir(parents=True)

    res = _meta_task(str(tmp_path), "task_unmatched")
    assert res == 1
    err = capsys.readouterr().err
    assert "No jobs found for task task_unmatched." in err


def test_meta_task_happy_path_jobs(tmp_path, capsys):
    """_meta_task aggregates tokens, cost, duration across matching jobs."""
    jobs_dir = tmp_path / ".snodo" / "jobs"
    job1_dir = jobs_dir / "j_job1"
    job1_dir.mkdir(parents=True)
    job2_dir = jobs_dir / "j_job2"
    job2_dir.mkdir(parents=True)

    task_data = {"task_id": "task_shared", "description": "Shared task description"}
    (job1_dir / "task.json").write_text(json.dumps(task_data))
    (job2_dir / "task.json").write_text(json.dumps(task_data))

    (job1_dir / "state.json").write_text(json.dumps({
        "started_at": 100.0,
        "completed_at": 105.0,
        "usage": [{"prompt_tokens": 500, "completion_tokens": 100, "cost": 0.005}],
        "halt": {"final_decision": "completed", "artifacts_count": 1},
    }))

    (job2_dir / "state.json").write_text(json.dumps({
        "started_at": 110.0,
        "completed_at": 120.0,
        "usage": [{"prompt_tokens": 1000, "completion_tokens": 200, "cost": 0.010}],
        "halt": {"final_decision": "completed", "artifacts_count": 2},
    }))

    res = _meta_task(str(tmp_path), "task_shared")
    assert res == 0

    out = capsys.readouterr().out
    assert "Task task_shared  2 job(s)  [completed]  total 20.0s" in out
    assert "Tokens: 1.8k (prompt 1.5k / completion 300)" in out
    assert "Cost: $0.0150" in out
    assert "j_job1" in out
    assert "j_job2" in out


def test_meta_task_foreground_telemetry(tmp_path, capsys):
    """_meta_task displays telemetry and usage from foreground task state."""
    task_dir = tmp_path / ".snodo" / "tasks" / "task_fg123"
    task_dir.mkdir(parents=True)

    task_state = {
        "task_id": "task_fg123",
        "description": "Implement foreground telemetry test",
        "status": "completed",
        "started_at": 200.0,
        "completed_at": 208.5,
        "usage": [
            {"role": "coder", "prompt_tokens": 1200, "completion_tokens": 400, "cost": 0.012},
        ],
        "tool_telemetry": [
            {
                "task_ref": "task_fg123",
                "role": "coder",
                "turn_index": 1,
                "tool": "read_files",
                "target_path": "src/module.py",
                "read_hit": False,
                "tokens_in": 600,
                "tokens_out": 100,
                "elapsed_ms": 500.0,
                "submit_bytes": 0,
            },
            {
                "task_ref": "task_fg123",
                "role": "coder",
                "turn_index": 2,
                "tool": "submit_files",
                "target_path": "",
                "read_hit": False,
                "tokens_in": 600,
                "tokens_out": 300,
                "elapsed_ms": 800.0,
                "submit_bytes": 1024,
            },
        ],
        "halt": {"final_decision": "completed", "artifacts_count": 1},
    }
    (task_dir / "state.json").write_text(json.dumps(task_state))

    res = _meta_task(str(tmp_path), "task_fg123")
    assert res == 0

    out = capsys.readouterr().out
    assert "Task task_fg123  [completed]  8.5s" in out
    assert "Task: Implement foreground telemetry test" in out
    assert "Tokens: 1.6k (prompt 1.2k / completion 400)" in out
    assert "Cost: $0.0120" in out
    assert "Orientation: 1/2 turns before first submit" in out
    assert "Path miss rate: 1/1 reads were misses" in out
    assert "Submit size: 1 submit(s), median 1024 bytes, max 1024 bytes" in out


def test_meta_task_json_output(tmp_path, capsys):
    """_meta_task with json_out=True emits snodo.meta.v1 schema with tool_telemetry."""
    task_dir = tmp_path / ".snodo" / "tasks" / "task_json_test"
    task_dir.mkdir(parents=True)

    task_state = {
        "task_id": "task_json_test",
        "description": "Test JSON output schema",
        "status": "completed",
        "started_at": 10.0,
        "completed_at": 15.0,
        "usage": [
            {"role": "coder", "prompt_tokens": 500, "completion_tokens": 200, "cost": 0.005},
        ],
        "tool_telemetry": [
            {
                "task_ref": "task_json_test",
                "role": "coder",
                "turn_index": 1,
                "tool": "read_files",
                "target_path": "src/main.py",
                "read_hit": False,
                "tokens_in": 300,
                "tokens_out": 50,
                "elapsed_ms": 400.0,
                "submit_bytes": 0,
            },
        ],
        "halt": {"final_decision": "completed", "artifacts_count": 1},
    }
    (task_dir / "state.json").write_text(json.dumps(task_state))

    res = _meta_task(str(tmp_path), "task_json_test", json_out=True)
    assert res == 0

    data = json.loads(capsys.readouterr().out)
    assert data["schema"] == "snodo.meta.v1"
    assert data["ok"] is True
    assert data["id"] == "task_json_test"
    assert data["type"] == "task"
    assert data["status"] == "completed"
    assert data["tokens"]["total"] == 700
    assert len(data["tool_telemetry"]) == 1
    assert data["tool_telemetry"][0]["tool"] == "read_files"
    assert "tool_telemetry_summary" in data
    assert "path_miss_rate" in data["tool_telemetry_summary"]


# ============================================================================
# 4. Routing & Prefix Dispatch Tests
# ============================================================================

def test_meta_command_routing(tmp_path, monkeypatch):
    """meta_command routes to _meta_job for j_ prefix and _meta_task for task_ prefix."""
    monkeypatch.setattr("snodo.cli.commands.meta_cmd.resolve_project_root", lambda: str(tmp_path))

    job_called = []
    task_called = []

    monkeypatch.setattr("snodo.cli.commands.meta_cmd._meta_job", lambda root, jid, json_out=False: job_called.append((jid, json_out)) or 0)
    monkeypatch.setattr("snodo.cli.commands.meta_cmd._meta_task", lambda root, tid, force=False, json_out=False: task_called.append((tid, force, json_out)) or 0)

    # j_ prefix -> job
    meta_command(SimpleNamespace(composite_id="j_abc", json=False))
    assert job_called == [("j_abc", False)]

    # task_ prefix -> task
    meta_command(SimpleNamespace(composite_id="task_xyz", json=True))
    assert task_called == [("task_xyz", False, True)]

    # Unknown prefix -> fallback logic
    job_dir = tmp_path / ".snodo" / "jobs" / "custom_job"
    job_dir.mkdir(parents=True)
    meta_command(SimpleNamespace(composite_id="custom_job", json=False))
    assert job_called == [("j_abc", False), ("custom_job", False)]


# ============================================================================
# 5. Helper Functions Unit Tests
# ============================================================================

def test_helpers_formatting():
    """Test format helpers: _duration, _fmt_tokens, _fmt_cost, _summarize_cost, _summarize_tokens, _highlight."""
    assert _duration(10.0, 15.5) == "5.5s"
    assert _duration(0, 0) == "—"

    assert _fmt_tokens(500) == "500"
    assert _fmt_tokens(1500) == "1.5k"

    assert _fmt_cost(None) == "unknown"
    assert _fmt_cost("invalid") == "unknown"
    assert _fmt_cost(0.01234) == "$0.0123"

    cost_str, total, partial = _summarize_cost([
        {"cost": 0.01},
        {"cost": None},
    ])
    assert partial is True
    assert "partial ($0.0100)" in cost_str

    p, c, tot = _summarize_tokens([
        {"prompt_tokens": 10, "completion_tokens": 5},
        {"prompt_tokens": 20, "completion_tokens": 10},
    ])
    assert (p, c, tot) == (30, 15, 45)

    roles = _per_role_tokens([
        {"role": "coder", "prompt_tokens": 100, "completion_tokens": 50},
        {"role": "reviewer", "prompt_tokens": 200, "completion_tokens": 100},
    ])
    assert roles[0][0] == "reviewer"  # sorted by total desc

    # Blocked highlight
    hl_blocker = _highlight(
        {
            "final_decision": "blocker",
            "phase": "pre_execute",
            "pre_validation": {
                "validator_results": [
                    {"severity": "blocker", "validator_id": "security", "justification": "Forbidden file write"}
                ]
            },
        },
        1000,
        "$0.01",
    )
    assert "blocked at pre_execute: security — Forbidden file write" in hl_blocker

    # Escalated highlight
    hl_escalated = _highlight({"final_decision": "escalate", "phase": "post_execute"}, 1000, "$0.01")
    assert "escalated at post_execute: needs human review" in hl_escalated
