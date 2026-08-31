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

    def test_unknown_job_is_noop(self, tmp_path, monkeypatch):
        from snodo.infrastructure.tool_telemetry import persist_tool_telemetry

        monkeypatch.setenv("SNODO_PROJECT_ROOT", str(tmp_path))
        # Must not raise.
        persist_tool_telemetry("unknown", {"turn_index": 1})


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
