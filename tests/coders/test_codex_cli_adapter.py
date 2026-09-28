import json
from unittest.mock import patch

import pytest

from snodo.coders import CODER_REGISTRY, resolve_coder_name
from snodo.coders.codex_cli_adapter import CodexCLIAdapter


def test_argv_and_model(tmp_path):
    adapter = CodexCLIAdapter(model="codex-cli/o4-mini", workspace=tmp_path)
    assert adapter._bare_model() == "o4-mini"
    assert adapter._build_argv("do work", str(tmp_path), adapter._bare_model()) == [
        "codex", "exec", "--json", "--sandbox", "workspace-write", "--cd",
        str(tmp_path), "--model", "o4-mini", "do work",
    ]


def test_usage_accumulates_across_turns(tmp_path):
    adapter = CodexCLIAdapter(workspace=tmp_path)
    adapter._begin_subprocess_run()
    assert adapter._process_output_line(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Done"}})) == "Done"
    adapter._process_output_line(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "cached_input_tokens": 3, "output_tokens": 4, "reasoning_output_tokens": 2}, "model": "gpt-5-codex"}))
    adapter._process_output_line(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 5, "cached_input_tokens": 1, "output_tokens": 6, "reasoning_output_tokens": 3}}))
    assert adapter.last_usage == {"input_tokens": 15, "cache_read_tokens": 4, "output_tokens": 10, "reasoning_tokens": 5, "cache_write_tokens": None, "cost": None, "served_model": "gpt-5-codex"}


def test_empty_usage_and_malformed_line(tmp_path):
    adapter = CodexCLIAdapter(workspace=tmp_path)
    adapter._begin_subprocess_run()
    assert adapter._process_output_line('{"type":"turn.completed"}') == ""
    assert all(value is None for value in adapter.last_usage.values())
    assert adapter._process_output_line("not-json\n") == "not-json"


def test_registration_and_availability():
    assert CODER_REGISTRY["codex-cli"] is CodexCLIAdapter
    assert resolve_coder_name("codex-cli/gpt-5-codex") == "codex-cli"
    assert CodexCLIAdapter.availability_requirements()[0][0] == "codex"


def test_missing_codex_binary_is_reported(tmp_path):
    from snodo.coders.base import CoderUnavailableError
    from snodo.core.interfaces import TaskSpec

    adapter = CodexCLIAdapter(workspace=tmp_path)
    with patch("subprocess.Popen", side_effect=FileNotFoundError):
        with pytest.raises(CoderUnavailableError, match="codex not found"):
            adapter.implement(TaskSpec(description="do work", constraints=[]))
