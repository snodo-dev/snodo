"""Served-model provenance: records carry what the provider said it served
beside what was asked for (Fixes #381).

Two invariants, checked on every record a completion call produces:

1. When a response reports a different model than was requested, BOTH appear
   in the record — the requested name and the served name, side by side.
   (The equal case is recorded the same way; it is the evidence that nothing
   changed.)
2. When a response reports nothing usable, the record SAYS SO
   (``served_model: None``) rather than copying the requested name in — an
   uninformative provider must not read as a matching one, nor as a
   substituting one.

This is provenance only: no test here (and no code under test) treats a
mismatch as an error, warning, state or verdict.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from snodo.coders.litellm import LiteLLMAdapter
from snodo.compiler.models import Validator
from snodo.core.interfaces import Task
from snodo.infrastructure.usage_tracker import UsageTracker
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


def _response(tool_calls=None, content="", usage=None, model="keep-magicmock"):
    """Mock litellm response; model=None simulates an uninformative provider."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = content
    resp.choices[0].message.tool_calls = tool_calls or []
    resp.choices[0].finish_reason = "tool_calls" if tool_calls else "stop"
    resp.usage = usage
    if model == "keep-magicmock":
        resp.model = "unreported-is-not-mock"  # placeholder, overwritten below
        del resp.model  # attribute absent → getattr falls to default None path
    else:
        resp.model = model
    return resp


def _tool_call(name, args):
    tc = MagicMock()
    tc.id = f"call_{name}"
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


def _usage(prompt=10, completion=5):
    u = MagicMock()
    u.prompt_tokens = prompt
    u.completion_tokens = completion
    return u


class TestServedModelHelper:
    def test_reports_string_verbatim(self):
        from snodo.infrastructure.model_provenance import served_model_of

        assert served_model_of(_response(model="gpt-4o-2024-11-20")) == "gpt-4o-2024-11-20"

    def test_absent_or_blank_is_none_not_a_guess(self):
        from snodo.infrastructure.model_provenance import served_model_of

        assert served_model_of(_response(model=None)) is None
        assert served_model_of(_response(model="")) is None
        assert served_model_of(_response(model="   ")) is None
        # A mock whose .model attribute was never set is not a reported name.
        bare = MagicMock()
        del bare.model
        assert served_model_of(bare) is None


class TestUsageTrackerRecord:
    """The per-completion usage record in state.json."""

    def _record_for(self, tmp_path, monkeypatch, model_attr):
        monkeypatch.setenv("SNODO_PROJECT_ROOT", str(tmp_path))
        task_id = "task_served_model_prov"
        tracker = UsageTracker()
        resp = _response(usage=_usage(), model=model_attr)
        tracker.log_success_event(
            {"metadata": {"task_id": task_id}, "model": "asked-model"},
            resp,
            0,
            1,
        )
        state = json.loads(
            (tmp_path / ".snodo" / "tasks" / task_id / "state.json").read_text()
        )
        return state["usage"][0]

    def test_substituted_model_appears_beside_requested(self, tmp_path, monkeypatch):
        record = self._record_for(tmp_path, monkeypatch, "served-deploy-7@v3")
        assert record["model"] == "asked-model"
        assert record["served_model"] == "served-deploy-7@v3"

    def test_uninformative_provider_recorded_as_absent(self, tmp_path, monkeypatch):
        record = self._record_for(tmp_path, monkeypatch, None)
        assert record["model"] == "asked-model"
        # The record says the provider reported nothing — it does NOT
        # assume a match by copying the requested name in.
        assert "served_model" in record
        assert record["served_model"] is None

    def test_match_case_records_both(self, tmp_path, monkeypatch):
        record = self._record_for(tmp_path, monkeypatch, "asked-model")
        assert record["model"] == "asked-model"
        assert record["served_model"] == "asked-model"


class TestCoderTurnTelemetry:
    """The per-turn coder record in state.json."""

    def _records(self, tmp_path, monkeypatch, served):
        project_root = str(tmp_path)
        job_id = "j_coder_prov"
        _make_job_dir(project_root, job_id)
        monkeypatch.setenv("SNODO_PROJECT_ROOT", project_root)

        adapter = LiteLLMAdapter(max_tool_turns=3)
        adapter.workspace_mcp = MagicMock()
        adapter._job_id = job_id
        adapter._task_id = "task_1"

        resp1 = _response(
            [_tool_call("read_file", {"path": "src/app.py"})],
            usage=_usage(), model=served,
        )
        resp2 = _response(
            [_tool_call("submit_files", {"files": [{"path": "src/app.py", "content": "x"}]})],
            usage=_usage(), model=served,
        )
        resp3 = _response([], usage=_usage(), model=served)
        adapter._completion_fn = MagicMock(side_effect=[resp1, resp2, resp3])
        adapter._execute_tool = MagicMock(return_value="file content")

        adapter._call_llm_with_tools("prompt")
        requested = adapter._completion_fn.call_args_list[0].kwargs["model"]
        return _read_telemetry(project_root, job_id), requested

    def test_substituted_model_appears_beside_requested(self, tmp_path, monkeypatch):
        records, requested = self._records(tmp_path, monkeypatch, "served-versioned@2")
        assert records
        for rec in records:
            assert rec["model"] == requested
            assert rec["served_model"] == "served-versioned@2"

    def test_uninformative_provider_recorded_as_absent(self, tmp_path, monkeypatch):
        records, requested = self._records(tmp_path, monkeypatch, None)
        assert records
        for rec in records:
            assert rec["model"] == requested
            assert "served_model" in rec
            assert rec["served_model"] is None


class TestValidatorTurnTelemetry:
    """The per-turn validator record in state.json."""

    def _records(self, tmp_path, monkeypatch, served):
        project_root = str(tmp_path)
        job_id = "j_validator_prov"
        _make_job_dir(project_root, job_id)
        monkeypatch.setenv("SNODO_PROJECT_ROOT", project_root)

        validator = LLMValidator(validator_spec=Validator(
            validator_id="quality", validator_type="quality",
            criteria=["c"], tools=["read_file"],
        ))
        validator._job_id = job_id
        validator._task_id = "task_1"

        resp1 = _response(
            [_tool_call("read_file", {"path": "src/main.py"})],
            usage=_usage(), model=served,
        )
        resp2 = _response(
            [_tool_call("submit_verdict", {"severity": "pass", "justification": "ok"})],
            usage=_usage(), model=served,
        )
        validator._completion_fn = MagicMock(side_effect=[resp1, resp2])

        ctx = ValidatorContext(
            task=Task(id="task_1", spec="test task", depth=0),
            completion_fn=validator._completion_fn,
            workspace_mcp=MagicMock(),
            git_mcp=MagicMock(),
            max_tool_turns=5,
            job_id=job_id,
            task_id="task_1",
        )
        result = validator._evaluate_with_tools(ctx)
        assert result.severity == "pass"
        requested = validator._completion_fn.call_args_list[0].kwargs["model"]
        return _read_telemetry(project_root, job_id), requested

    def test_substituted_model_appears_beside_requested(self, tmp_path, monkeypatch):
        records, requested = self._records(tmp_path, monkeypatch, "judge-served-2026-08")
        assert records
        for rec in records:
            assert rec["model"] == requested
            assert rec["served_model"] == "judge-served-2026-08"

    def test_uninformative_provider_recorded_as_absent(self, tmp_path, monkeypatch):
        records, requested = self._records(tmp_path, monkeypatch, None)
        assert records
        for rec in records:
            assert rec["model"] == requested
            assert "served_model" in rec
            assert rec["served_model"] is None


class TestReconResultRecord:
    """The recon result record written to results.json."""

    def _result(self, served):
        from snodo.config import ConfigManager
        from snodo.recon import call_agent

        with patch.object(ConfigManager, "get_key_for_model", lambda self, m: "k"), \
             patch.object(ConfigManager, "resolve_litellm_model", staticmethod(lambda m: m)), \
             patch.object(ConfigManager, "resolve_api_base", staticmethod(lambda m: None)), \
             patch.object(ConfigManager, "resolve_extra_headers", staticmethod(lambda m, task_id=None: None)), \
             patch("litellm.completion", return_value=_response(content="an answer", model=served)):
            return call_agent(".", "asked-model", "query", [], "agent", max_turns=2)

    def test_substituted_model_appears_beside_requested(self):
        result = self._result("served-fingerprint-xyz")
        assert result.model == "asked-model"
        assert result.served_model == "served-fingerprint-xyz"

    def test_uninformative_provider_recorded_as_absent(self):
        result = self._result(None)
        assert result.model == "asked-model"
        assert "served_model" in result.model_dump()
        assert result.served_model is None
