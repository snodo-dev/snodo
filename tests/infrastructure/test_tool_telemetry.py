"""Tests for per-turn tool-loop telemetry (Fixes #105).

Covers:
- The state.json sink (persist_tool_telemetry).
- Coder loop emission (LiteLLMAdapter._call_llm_with_tools).
- Validator loop emission (LLMValidator._evaluate_with_tools).
- The `snodo meta` tool-loop summary.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

from snodo.coders.litellm import LiteLLMAdapter
from snodo.compiler.models import Validator
from snodo.core.interfaces import Task
from snodo.validators.context import ValidatorContext
from snodo.validators.llm_validator import LLMValidator


def _make_job_dir(project_root: str, job_id: str) -> Path:
    job_dir = Path(project_root) / ".snodo" / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "state.json").write_text("{}")
    return job_dir


def _read_telemetry(project_root: str, job_id: str) -> list:
    state = json.loads((Path(project_root) / ".snodo" / "jobs" / job_id / "state.json").read_text())
    return state.get("tool_telemetry", [])


def _make_response(tool_calls, content="", usage=None):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = tool_calls
    resp.choices[0].finish_reason = "tool_calls"
    resp.usage = usage
    return resp


def _tool_call(name, args):
    tc = MagicMock()
    tc.id = f"call_{name}"
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


class TestToolTelemetrySink:
    def test_canonical_target_path(self):
        from snodo.infrastructure.tool_telemetry import canonical_target_path

        assert canonical_target_path("./src/app.py") == "src/app.py"
        assert canonical_target_path("src/app.py") == "src/app.py"
        assert canonical_target_path("src\\app.py") == "src/app.py"
        assert canonical_target_path("") == ""
        assert canonical_target_path(None) == ""

    def test_persist_appends_to_job_state(self, tmp_path, monkeypatch):
        from snodo.infrastructure.tool_telemetry import persist_tool_telemetry

        project_root = str(tmp_path)
        job_id = "j_telemetry_1"
        _make_job_dir(project_root, job_id)
        monkeypatch.setenv("SNODO_PROJECT_ROOT", project_root)

        persist_tool_telemetry(job_id, {"turn_index": 1, "tool": "read_file"})
        persist_tool_telemetry(job_id, {"turn_index": 2, "tool": "submit_files"})

        records = _read_telemetry(project_root, job_id)
        assert len(records) == 2
        assert records[0]["turn_index"] == 1
        assert records[1]["tool"] == "submit_files"

    def test_persist_appends_to_task_state(self, tmp_path, monkeypatch):
        from snodo.infrastructure.tool_telemetry import persist_tool_telemetry

        project_root = str(tmp_path)
        task_id = "task_foreground_01"
        monkeypatch.setenv("SNODO_PROJECT_ROOT", project_root)

        persist_tool_telemetry("unknown", {"task_ref": task_id, "turn_index": 1, "tool": "read_files"})
        persist_tool_telemetry(task_id, {"turn_index": 2, "tool": "submit_files"})

        task_state_path = tmp_path / ".snodo" / "tasks" / task_id / "state.json"
        assert task_state_path.exists()
        data = json.loads(task_state_path.read_text())
        assert len(data.get("tool_telemetry", [])) == 2
        assert data["tool_telemetry"][0]["tool"] == "read_files"
        assert data["tool_telemetry"][1]["tool"] == "submit_files"

    def test_unknown_job_is_noop(self, tmp_path, monkeypatch):
        from snodo.infrastructure.tool_telemetry import persist_tool_telemetry

        monkeypatch.setenv("SNODO_PROJECT_ROOT", str(tmp_path))
        # Must not raise.
        persist_tool_telemetry("unknown", {"turn_index": 1})

    def test_telemetry_write_lock_failure_is_silent(self, tmp_path, monkeypatch):
        """A telemetry write that cannot take its lock is still silent and does not raise."""
        from unittest.mock import patch
        from snodo.infrastructure.tool_telemetry import persist_tool_telemetry

        project_root = str(tmp_path)
        job_id = "j_telemetry_flock_fail"
        _make_job_dir(project_root, job_id)
        monkeypatch.setenv("SNODO_PROJECT_ROOT", project_root)

        with patch("fcntl.flock", side_effect=OSError("Lock unavailable")):
            # Telemetry must not raise when lock cannot be acquired
            persist_tool_telemetry(job_id, {"turn_index": 1, "tool": "read_file"})

    def test_record_with_no_resolvable_id_discarded_and_discoverable(self, tmp_path, monkeypatch, caplog):
        import logging
        from types import SimpleNamespace
        from snodo.cli.commands.meta_cmd import meta_command
        from snodo.infrastructure.tool_telemetry import (
            get_dropped_telemetry_count,
            get_dropped_telemetry_records,
            persist_tool_telemetry,
            reset_dropped_telemetry,
        )

        reset_dropped_telemetry()
        monkeypatch.setenv("SNODO_PROJECT_ROOT", str(tmp_path))
        monkeypatch.setattr("snodo.cli.commands.meta_cmd.resolve_project_root", lambda: str(tmp_path))

        with caplog.at_level(logging.DEBUG):
            # No resolvable job_id ("unknown" does not start with j_) and no valid task_id
            persist_tool_telemetry("unknown", {"turn_index": 1, "tool": "read_files"})

        # In-process observability
        assert get_dropped_telemetry_count() == 1
        dropped = get_dropped_telemetry_records()
        assert len(dropped) == 1
        assert dropped[0]["reason"] == "no_target_id"
        assert dropped[0]["target_id"] == "unknown"
        assert dropped[0]["record"] == {"turn_index": 1, "tool": "read_files"}
        assert any("Dropped tool telemetry record (no_target_id)" in r.message for r in caplog.records)

        # Durable observability in .snodo/telemetry_drops.json
        drops_file = tmp_path / ".snodo" / "telemetry_drops.json"
        assert drops_file.exists()
        persisted_drops = json.loads(drops_file.read_text())
        assert len(persisted_drops.get("drops", [])) == 1
        assert persisted_drops["drops"][0]["reason"] == "no_target_id"

        # Confirm no job or task folders were created
        assert not (tmp_path / ".snodo" / "jobs").exists()
        assert not (tmp_path / ".snodo" / "tasks").exists()

        # Observable via meta when inspecting an empty task run
        task_dir = tmp_path / ".snodo" / "tasks" / "task_empty_run"
        task_dir.mkdir(parents=True)
        (task_dir / "state.json").write_text(json.dumps({"task_id": "task_empty_run", "status": "completed"}))

        import io
        import contextlib
        stdout_buf = io.StringIO()
        with contextlib.redirect_stdout(stdout_buf):
            meta_command(SimpleNamespace(composite_id="task_empty_run", json=False))
        out = stdout_buf.getvalue()
        assert "Tool-loop telemetry:" in out
        assert "no turns recorded; 1 dropped: 1 no_target_id" in out

    def test_foreground_task_telemetry_readable_through_meta(self, tmp_path, monkeypatch, capsys):
        from types import SimpleNamespace
        from snodo.cli.commands.meta_cmd import meta_command
        from snodo.infrastructure.tool_telemetry import persist_tool_telemetry

        project_root = str(tmp_path)
        task_id = "task_foreground_meta_pinned"
        monkeypatch.setenv("SNODO_PROJECT_ROOT", project_root)
        monkeypatch.setattr("snodo.cli.commands.meta_cmd.resolve_project_root", lambda: project_root)

        # Simulate per-turn telemetry emitted during foreground task run
        persist_tool_telemetry(
            task_id,
            {
                "task_ref": task_id,
                "role": "coder",
                "depth": 0,
                "attempt": 1,
                "turn_index": 1,
                "tool": "read_files",
                "target_path": "src/module.py",
                "read_hit": False,
                "tokens_in": 120,
                "tokens_out": 40,
                "elapsed_ms": 100.0,
                "submit_bytes": 0,
            },
        )
        persist_tool_telemetry(
            "unknown",
            {
                "task_ref": task_id,
                "role": "coder",
                "depth": 0,
                "attempt": 1,
                "turn_index": 2,
                "tool": "submit_files",
                "target_path": "",
                "read_hit": False,
                "tokens_in": 150,
                "tokens_out": 60,
                "elapsed_ms": 250.0,
                "submit_bytes": 1024,
            },
        )

        # Verify state file was created
        state_file = tmp_path / ".snodo" / "tasks" / task_id / "state.json"
        assert state_file.exists()

        # 1. Read back via snodo meta <task_id> (human display)
        capsys.readouterr()  # clear buffer
        rc = meta_command(SimpleNamespace(composite_id=task_id, json=False))
        assert rc == 0
        out = capsys.readouterr().out
        assert f"Task {task_id}" in out
        assert "Tool-loop telemetry:" in out
        assert "Orientation: 1/2 turns before first submit (50% of coder turns)" in out
        assert "Path miss rate: 1/1 reads were misses (100%)" in out
        assert "Submit size: 1 submit(s), median 1024 bytes, max 1024 bytes" in out

        # 2. Read back via snodo meta <task_id> --json
        rc_json = meta_command(SimpleNamespace(composite_id=task_id, json=True))
        assert rc_json == 0
        json_out = json.loads(capsys.readouterr().out)
        assert json_out["ok"] is True
        assert json_out["id"] == task_id
        assert json_out["type"] == "task"
        assert len(json_out["tool_telemetry"]) == 2
        assert json_out["tool_telemetry"][0]["tool"] == "read_files"
        assert json_out["tool_telemetry"][1]["tool"] == "submit_files"
        assert json_out["tool_telemetry_summary"]["orientation"]["turns_before_first_submit"] == 1
        assert json_out["tool_telemetry_summary"]["path_miss_rate"]["misses"] == 1
        assert json_out["tool_telemetry_summary"]["submit_size"]["median_bytes"] == 1024

    def test_drop_file_stops_growing_at_cap(self, tmp_path, monkeypatch):
        """.snodo/telemetry_drops.json is bounded by the in-process cap (Refs #206).

        A run that drops every record must not turn the whole-file rewrite in
        atomic_update_json into a quadratic cost against the run it observes.
        The persisted global drop list stops growing at _MAX_DROPPED_RECORDS.
        """
        from snodo.infrastructure.tool_telemetry import (
            _MAX_DROPPED_RECORDS,
            persist_tool_telemetry,
            reset_dropped_telemetry,
        )

        reset_dropped_telemetry()
        monkeypatch.setenv("SNODO_PROJECT_ROOT", str(tmp_path))

        # Emit far more unresolvable records than the cap; each becomes a
        # no_target_id drop appended to the global file.
        for i in range(_MAX_DROPPED_RECORDS + 50):
            persist_tool_telemetry("unknown", {"turn_index": i, "tool": "read_files"})

        drops_file = tmp_path / ".snodo" / "telemetry_drops.json"
        assert drops_file.exists()
        persisted = json.loads(drops_file.read_text())
        assert len(persisted["drops"]) == _MAX_DROPPED_RECORDS

        # The cap keeps the most recent drops, not the first ones.
        assert persisted["drops"][-1]["record"]["turn_index"] == _MAX_DROPPED_RECORDS + 49

    def test_unconfigured_project_root_is_silent_noop(self, monkeypatch):
        from snodo.infrastructure.tool_telemetry import persist_tool_telemetry
        from snodo.infrastructure.usage_tracker import UsageTracker

        monkeypatch.delenv("SNODO_PROJECT_ROOT", raising=False)
        monkeypatch.delenv("SNODO_JOB_ID", raising=False)

        # Must not create .snodo anywhere or raise
        persist_tool_telemetry("task_123", {"turn_index": 1, "tool": "read_files"})

        tracker = UsageTracker()
        tracker.log_success_event(
            {"metadata": {"task_id": "task_123"}},
            MagicMock(usage=MagicMock(prompt_tokens=10, completion_tokens=10)),
            0,
            1,
        )

    def test_usage_tracker_handles_timedelta(self, tmp_path, monkeypatch):
        import datetime
        from snodo.infrastructure.usage_tracker import UsageTracker

        project_root = str(tmp_path)
        task_id = "task_timedelta_test"
        monkeypatch.setenv("SNODO_PROJECT_ROOT", project_root)

        tracker = UsageTracker()
        start = datetime.datetime(2026, 9, 1, 12, 0, 0)
        end = datetime.datetime(2026, 9, 1, 12, 0, 2, 500000)  # 2.5s timedelta

        resp = MagicMock()
        resp.usage.prompt_tokens = 20
        resp.usage.completion_tokens = 30

        tracker.log_success_event(
            {"metadata": {"task_id": task_id}, "model": "test-model"},
            resp,
            start,
            end,
        )

        task_state_path = tmp_path / ".snodo" / "tasks" / task_id / "state.json"
        assert task_state_path.exists()
        data = json.loads(task_state_path.read_text())
        assert len(data.get("usage", [])) == 1
        assert data["usage"][0]["duration_ms"] == 2500.0

    def test_concurrent_telemetry_and_usage_writes(self, tmp_path, monkeypatch):
        import concurrent.futures
        import datetime
        from snodo.infrastructure.tool_telemetry import persist_tool_telemetry
        from snodo.infrastructure.usage_tracker import UsageTracker

        project_root = str(tmp_path)
        task_id = "task_concurrent_test"
        monkeypatch.setenv("SNODO_PROJECT_ROOT", project_root)

        tracker = UsageTracker()

        def _write_telemetry(idx):
            persist_tool_telemetry(
                task_id,
                {"turn_index": idx, "tool": f"tool_{idx}", "task_ref": task_id},
            )

        def _write_usage(idx):
            resp = MagicMock()
            resp.usage.prompt_tokens = idx
            resp.usage.completion_tokens = idx
            tracker.log_success_event(
                {"metadata": {"task_id": task_id}, "model": "test-model"},
                resp,
                datetime.datetime.now(),
                datetime.datetime.now() + datetime.timedelta(seconds=1),
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futs = []
            for i in range(20):
                futs.append(executor.submit(_write_telemetry, i))
                futs.append(executor.submit(_write_usage, i))
            for f in futs:
                f.result()

        task_state_path = tmp_path / ".snodo" / "tasks" / task_id / "state.json"
        assert task_state_path.exists()
        data = json.loads(task_state_path.read_text())
        assert len(data.get("tool_telemetry", [])) == 20
        assert len(data.get("usage", [])) == 20


class TestCoderTelemetry:
    def test_coder_emits_per_turn_records(self, tmp_path, monkeypatch):
        project_root = str(tmp_path)
        job_id = "j_coder_telemetry"
        _make_job_dir(project_root, job_id)
        monkeypatch.setenv("SNODO_PROJECT_ROOT", project_root)

        adapter = LiteLLMAdapter(max_tool_turns=3)
        adapter.workspace_mcp = MagicMock()
        adapter._job_id = job_id
        adapter._task_id = "task_1"
        adapter._depth = 0
        adapter._attempt = 1

        usage = MagicMock()
        usage.prompt_tokens = 10
        usage.completion_tokens = 5

        resp1 = _make_response(
            [_tool_call("read_file", {"path": "src/app.py"})],
            usage=usage,
        )
        resp2 = _make_response(
            [_tool_call("submit_files", {"files": [{"path": "src/app.py", "content": "x"}]})],
            usage=usage,
        )
        resp3 = _make_response([])
        adapter._completion_fn = MagicMock(side_effect=[resp1, resp2, resp3])
        adapter._execute_tool = MagicMock(return_value="file content")

        adapter._call_llm_with_tools("prompt")

        records = _read_telemetry(project_root, job_id)
        assert len(records) == 2
        read_rec = records[0]
        assert read_rec["role"] == "coder"
        assert read_rec["task_ref"] == "task_1"
        assert read_rec["depth"] == 0
        assert read_rec["attempt"] == 1
        assert read_rec["turn_index"] == 1
        assert read_rec["tool"] == "read_file"
        assert read_rec["target_path"] == "src/app.py"
        assert read_rec["read_hit"] is False
        assert read_rec["tokens_in"] == 10
        assert read_rec["tokens_out"] == 5

        submit_rec = records[1]
        assert submit_rec["tool"] == "submit_files"
        assert submit_rec["submit_bytes"] > 0


class TestValidatorTelemetry:
    def test_validator_emits_per_turn_records(self, tmp_path, monkeypatch):
        project_root = str(tmp_path)
        job_id = "j_validator_telemetry"
        _make_job_dir(project_root, job_id)
        monkeypatch.setenv("SNODO_PROJECT_ROOT", project_root)

        val_spec = Validator(
            validator_id="quality",
            validator_type="quality",
            criteria=["Ensure quality"],
            tools=["read_file"],
        )
        validator = LLMValidator(validator_spec=val_spec)
        validator._job_id = job_id
        validator._task_id = "task_1"
        validator._depth = 1
        validator._attempt = 2

        usage = MagicMock()
        usage.prompt_tokens = 7
        usage.completion_tokens = 3

        resp1 = _make_response(
            [_tool_call("read_file", {"path": "src/main.py"})],
            usage=usage,
        )
        resp2 = _make_response(
            [_tool_call("submit_verdict", {"severity": "pass", "justification": "ok"})],
            usage=usage,
        )
        validator._completion_fn = MagicMock(side_effect=[resp1, resp2])

        ctx = ValidatorContext(
            task=Task(id="task_1", spec="test task", depth=1),
            completion_fn=validator._completion_fn,
            workspace_mcp=MagicMock(),
            git_mcp=MagicMock(),
            max_tool_turns=5,
            job_id=job_id,
            task_id="task_1",
        )

        result = validator._evaluate_with_tools(ctx)
        assert result.severity == "pass"

        records = _read_telemetry(project_root, job_id)
        assert len(records) == 2
        read_rec = records[0]
        assert read_rec["role"] == "validator"
        assert read_rec["validator_id"] == "quality"
        assert read_rec["depth"] == 1
        assert read_rec["attempt"] == 2
        assert read_rec["tool"] == "read_file"
        assert read_rec["target_path"] == "src/main.py"

        verdict_rec = records[1]
        assert verdict_rec["tool"] == "submit_verdict"


class TestMetaTelemetrySummary:
    def test_meta_reports_telemetry(self, tmp_path, monkeypatch, capsys):
        from snodo.cli.commands.meta_cmd import _tool_telemetry_summary

        records = [
            {"role": "coder", "depth": 0, "turn_index": 1, "tool": "read_file",
             "target_path": "docs/plan.md", "read_hit": False, "tokens_in": 1, "tokens_out": 1,
             "elapsed_ms": 1.0, "submit_bytes": 0},
            {"role": "coder", "depth": 0, "turn_index": 2, "tool": "read_file",
             "target_path": "src/app.py", "read_hit": False, "tokens_in": 1, "tokens_out": 1,
             "elapsed_ms": 1.0, "submit_bytes": 0},
            {"role": "coder", "depth": 0, "turn_index": 3, "tool": "read_file",
             "target_path": "src/app.py", "read_hit": True, "tokens_in": 1, "tokens_out": 1,
             "elapsed_ms": 1.0, "submit_bytes": 0},
            {"role": "coder", "depth": 0, "turn_index": 4, "tool": "submit_files",
             "target_path": "", "read_hit": False, "tokens_in": 1, "tokens_out": 1,
             "elapsed_ms": 1.0, "submit_bytes": 2048},
        ]

        lines = _tool_telemetry_summary(records)
        joined = "\n".join(lines)
        assert "Orientation: 3/4 turns before first submit" in joined
        assert "Path miss rate: 2/3 reads were misses" in joined
        assert "Re-read by depth: depth 0: 1/3 re-reads" in joined
        assert "Submit size: 1 submit(s), median 2048 bytes, max 2048 bytes" in joined
