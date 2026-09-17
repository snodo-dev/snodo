"""An orchestrator records a task's status outside the loop.

FILE: tests/mcp/test_record_task_status.py

The MCP `record_task_status` tool and `snodo task complete` are two surfaces
over one implementation (`PlannerMCP.record_status`). The claims tested here:

- recording a status through the tool and through the CLI leaves the same plan
  state and the same audit entry (same vocabulary, same provenance),
- an invalid status is refused by the tool exactly as the CLI refuses it,
- a wave advances from a recorded status as it would have from a CLI record,
- a recorded status is an operator's account, never a validator verdict: the
  audit entry is marked unjudged/outside the loop and distinguishable from an
  engine completion.
"""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from snodo.compiler.models import Protocol
from snodo.infrastructure.audit import AuditLog
from snodo.mcp.server import MCPError, ProtocolMCPServer

_PROTOCOL_DATA = {
    "protocol_id": "record_status_test",
    "name": "Record Status Test Protocol",
    "version": "1.0.0",
    "initial_mode": "producer",
    "modes": [
        {
            "mode_id": "producer",
            "name": "Producer",
            "tools": ["edit", "test"],
            "validators": ["quality"],
        },
    ],
    "validators": [
        {
            "validator_id": "quality",
            "validator_type": "quality",
            "tooling": {"test_command": "echo test passed"},
            "criteria": ["Tests pass"],
        },
    ],
    "disagreement_policy": "unanimous",
}


def _make_project_with_plan(tmp_path: Path, plan_name: str) -> Path:
    """A git project with one two-wave plan whose only task starts blocked."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("test")
    snodo_dir = tmp_path / ".snodo"
    snodo_dir.mkdir()
    (snodo_dir / "protocol.yml").write_text(yaml.dump(_PROTOCOL_DATA))

    plan_dir = snodo_dir / "plans" / plan_name
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.yml").write_text(yaml.dump({
        "name": plan_name,
        "intent": "Record a hand resolution",
        "waves": [
            {"id": 1, "depends_on": [], "tasks": ["1.1_task"]},
            {"id": 2, "depends_on": [1], "tasks": ["2.1_task"]},
        ],
    }))
    (plan_dir / "status.json").write_text(json.dumps({
        "tasks": {
            "1.1_task": {"status": "blocked"},
            "2.1_task": {"status": "pending"},
        },
    }))

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    return plan_dir


@pytest.fixture
def project_dir(tmp_path):
    _make_project_with_plan(tmp_path, "ship")
    yield str(tmp_path)
    shutil.rmtree(tmp_path, ignore_errors=True)


@pytest.fixture
def audit(tmp_path):
    return AuditLog(str(tmp_path / ".snodo" / "audit.log"))


@pytest.fixture
def server(project_dir, audit):
    return ProtocolMCPServer(
        Protocol(**_PROTOCOL_DATA), project_dir, audit_log=audit,
    )


def _tool_record(server, **overrides):
    args = {
        "plan_name": "ship",
        "task_id": "1.1_task",
        "status": "completed",
        "who": "alice",
        "notes": "merged by hand",
    }
    args.update(overrides)
    return server.call_tool("record_task_status", args)


def _cli_record(project_dir, monkeypatch, audit, **overrides):
    from snodo.cli.commands.task_complete import task_complete_command

    monkeypatch.setattr(
        "snodo.infrastructure.audit.get_audit_log", lambda p=None: audit,
    )
    monkeypatch.setattr(
        "snodo.cli.commands.task_cmd.resolve_project_root", lambda: project_dir,
    )
    args = SimpleNamespace(
        task_id="1.1_task", plan="ship", who="alice",
        notes="merged by hand", json=False,
    )
    for key, value in overrides.items():
        setattr(args, key, value)
    return task_complete_command(args)


def _normalized_entry(entry: dict) -> dict:
    """A status.json entry without its timestamp — the part that varies."""
    return {k: v for k, v in entry.items() if k not in ("completed_at", "recorded_at")}


def _normalized_event(data: dict) -> dict:
    return {k: v for k, v in data.items() if k not in ("recorded_at", "timestamp")}


class TestSameEffectsAsTheCli:
    def test_tool_and_cli_leave_the_same_plan_state(
        self, tmp_path, monkeypatch, audit
    ):
        tool_root = tmp_path / "tool"
        cli_root = tmp_path / "cli"
        tool_root.mkdir()
        cli_root.mkdir()
        tool_plan = _make_project_with_plan(tool_root, "ship")
        cli_plan = _make_project_with_plan(cli_root, "ship")

        tool_server = ProtocolMCPServer(
            Protocol(**_PROTOCOL_DATA), str(tool_root), audit_log=audit,
        )
        _tool_record(tool_server)

        assert _cli_record(str(cli_root), monkeypatch, audit) == 0

        tool_status = json.loads((tool_plan / "status.json").read_text())
        cli_status = json.loads((cli_plan / "status.json").read_text())

        assert (
            _normalized_entry(tool_status["tasks"]["1.1_task"])
            == _normalized_entry(cli_status["tasks"]["1.1_task"])
        )
        assert tool_status["tasks"]["1.1_task"]["status"] == "completed"
        assert tool_status["tasks"]["1.1_task"]["completed_by"] == "alice"
        assert tool_status["tasks"]["1.1_task"]["judged"] is False

    def test_tool_and_cli_leave_the_same_audit_entry(
        self, tmp_path, monkeypatch, audit
    ):
        tool_root = tmp_path / "tool"
        cli_root = tmp_path / "cli"
        tool_root.mkdir()
        cli_root.mkdir()
        _make_project_with_plan(tool_root, "ship")
        _make_project_with_plan(cli_root, "ship")

        tool_server = ProtocolMCPServer(
            Protocol(**_PROTOCOL_DATA), str(tool_root), audit_log=audit,
        )
        _tool_record(tool_server)
        assert _cli_record(str(cli_root), monkeypatch, audit) == 0

        records = [
            e for e in audit.get_history()
            if (e.data or {}).get("op") == "task_completed_by_hand"
        ]
        assert len(records) == 2
        assert _normalized_event(records[0].data) == _normalized_event(records[1].data)
        assert records[0].event_type == "task_completed_by_hand"


class TestInvalidStatusRefused:
    def test_tool_refuses_a_status_outside_the_vocabulary(self, server, project_dir):
        with pytest.raises(MCPError, match="Invalid status"):
            _tool_record(server, status="hand_completed")

        # Nothing was written: the plan and the audit log are untouched.
        status = json.loads(
            (Path(project_dir) / ".snodo" / "plans" / "ship" / "status.json").read_text()
        )
        assert status["tasks"]["1.1_task"]["status"] == "blocked"
        assert not [
            e for e in server._audit_log.get_history()
            if (e.data or {}).get("op")
            in ("task_completed_by_hand", "task_status_recorded_by_hand")
        ]

    def test_tool_refuses_what_the_cli_refuses(self, server, tmp_path, monkeypatch, audit):
        """The same value refused by both surfaces, from the one vocabulary."""
        with pytest.raises(MCPError, match="Invalid status"):
            _tool_record(server, status="unknown")

        cli_root = tmp_path / "cli"
        cli_root.mkdir()
        _make_project_with_plan(cli_root, "ship")
        # The CLI's own writer uses the same guard; a direct call refuses too.
        from snodo.mcp.planner import PlannerError, PlannerMCP

        with pytest.raises(PlannerError, match="Invalid status"):
            PlannerMCP(str(cli_root)).update_status("ship", "1.1_task", "unknown")


class TestWaveAdvances:
    def test_a_recorded_status_advances_the_wave(self, server, project_dir):
        from snodo.cli.commands.plan_run import _get_completed_waves, _task_completed

        plan_yml = yaml.safe_load(
            (Path(project_dir) / ".snodo" / "plans" / "ship" / "plan.yml").read_text()
        )
        initial = json.loads(
            (Path(project_dir) / ".snodo" / "plans" / "ship" / "status.json").read_text()
        )["tasks"]
        assert not _task_completed(initial, "1.1_task")
        assert 1 not in _get_completed_waves(plan_yml["waves"], initial)

        _tool_record(server, status="completed")

        updated = json.loads(
            (Path(project_dir) / ".snodo" / "plans" / "ship" / "status.json").read_text()
        )["tasks"]
        assert _task_completed(updated, "1.1_task") is True
        completed_waves = _get_completed_waves(plan_yml["waves"], updated)
        assert 1 in completed_waves or "1" in completed_waves


class TestOperatorAccountNotAVerdict:
    def test_audit_entry_is_unjudged_and_distinguishable_from_engine(
        self, server, audit
    ):
        _tool_record(server)

        # An engine completion, as the loop writes it.
        audit.append_event("task_complete", {
            "op": "task_complete",
            "task_ref": "1.1_engine",
            "artifacts": ["snodo/models.py"],
        })

        hand = next(
            e for e in audit.get_history()
            if (e.data or {}).get("op") == "task_completed_by_hand"
        )
        engine = next(
            e for e in audit.get_history()
            if (e.data or {}).get("op") == "task_complete"
        )

        assert hand.data["judged"] is False
        assert hand.data["engine_judged"] is False
        assert hand.data["outside_loop"] is True
        assert hand.data["who"] == "alice"

        def is_engine_completion(event) -> bool:
            d = event.data or {}
            op = d.get("op") or event.event_type
            return op == "task_complete" and "who" not in d

        def is_operator_record(event) -> bool:
            d = event.data or {}
            op = d.get("op") or event.event_type
            return op in ("task_completed_by_hand", "hand_completed") \
                and d.get("judged") is False and "who" in d

        assert is_engine_completion(engine) and not is_operator_record(engine)
        assert is_operator_record(hand) and not is_engine_completion(hand)

    def test_a_missing_who_is_refused(self, server):
        with pytest.raises(MCPError, match="who"):
            _tool_record(server, who="")

    def test_an_unknown_plan_is_refused(self, server):
        with pytest.raises(MCPError, match="Plan not found"):
            _tool_record(server, plan_name="ghost")


class TestSurfaceExposure:
    def test_record_task_status_is_in_the_plan_grant_and_registry(self):
        from snodo.mcp.tools import MODE_TOOL_MAP, PLANNING_TOOLS, TOOL_REGISTRY

        assert "record_task_status" in TOOL_REGISTRY
        assert "record_task_status" in MODE_TOOL_MAP["plan"]
        assert "record_task_status" in PLANNING_TOOLS

    def test_all_modes_server_exposes_it(self, server):
        names = {t["name"] for t in server.get_tools()}
        assert "record_task_status" in names
