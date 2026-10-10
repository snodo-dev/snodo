import json
import os

from snodo.coders import CODER_REGISTRY, resolve_coder_name
from snodo.coders.claude_cli_adapter import ClaudeCLIAdapter


def test_claude_cli_routes_and_builds_noninteractive_command(tmp_path):
    adapter = ClaudeCLIAdapter(model="claude-cli/sonnet", workspace=tmp_path)
    assert CODER_REGISTRY["claude-cli"] is ClaudeCLIAdapter
    assert resolve_coder_name("claude-cli/sonnet") == "claude-cli"
    assert adapter._bare_model() == "sonnet"
    assert adapter._build_argv("prompt", str(tmp_path), "sonnet") == [
        "claude", "--model", "sonnet", "-p", "--output-format", "stream-json",
        "--verbose", "--permission-mode", "bypassPermissions", "--setting-sources",
        "user", "prompt",
    ]


def test_stream_json_output_and_usage(tmp_path):
    adapter = ClaudeCLIAdapter(workspace=tmp_path)
    adapter._begin_subprocess_run()
    assistant = {"type": "assistant", "message": {"content": [
        {"type": "text", "text": "Working"}, {"type": "tool_use", "name": "Edit"},
    ]}}
    assert adapter._process_output_line(json.dumps(assistant)) == "Working"
    assert adapter._process_output_line(json.dumps({
        "type": "result", "result": "Done", "model": "claude-sonnet-4",
        "total_cost_usd": 0.25,
        "usage": {"input_tokens": 10, "output_tokens": 4,
                  "cache_read_input_tokens": 3, "cache_creation_input_tokens": 2},
    })) == "Done"
    assert adapter.last_usage == {
        "input_tokens": 10, "output_tokens": 4, "reasoning_tokens": None,
        "cache_read_tokens": 3, "cache_write_tokens": 2, "cost": 0.25,
        "served_model": "claude-sonnet-4",
    }
    assert adapter._process_output_line("not-json") == "not-json"
    assert adapter._process_output_line("[]") == "[]"
    assert adapter._process_output_line(json.dumps({"type": "result", "usage": {"input_tokens": True}})) == ""
    assert adapter._process_output_line(json.dumps({
        "type": "stream_event", "event": {"delta": {"text": "partial"}},
    })) == "partial"
    assert adapter._process_output_line(json.dumps({
        "type": "stream_event", "event": {"delta": "unexpected"},
    })) == ""
    assert adapter._process_output_line(json.dumps({"type": "other", "text": "notice"})) == "notice"
    assert adapter._format_output_tail_stdout(json.dumps(assistant)) == "Working"
    assert adapter._build_argv("prompt", str(tmp_path), "")[-1] == "prompt"


def test_fake_claude_executable_runs_in_workspace_and_scrubs_job_context(tmp_path, monkeypatch):
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    fake_cli = executable_dir / "claude"
    fake_cli.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os\n"
        "print(json.dumps({'type':'assistant','message':{'content':[{'type':'text','text':os.getcwd()+' '+str(bool(os.getenv('SNODO_JOB_ID')))+' '+str(os.getenv('SNODO_PROJECT_ROOT'))+' '+str(os.getenv('SNODO_CODER_SUBPROCESS'))}]}}))\n"
        "print(json.dumps({'type':'result','result':'ok','usage':{'input_tokens':2}}))\n"
        ""
    )
    fake_cli.chmod(0o755)
    monkeypatch.setenv("PATH", f"{executable_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setenv("SNODO_JOB_ID", "j_private")
    monkeypatch.setenv("SNODO_PROJECT_ROOT", str(tmp_path / "real-project"))
    monkeypatch.delenv("SNODO_CODER_SUBPROCESS", raising=False)
    adapter = ClaudeCLIAdapter(workspace=tmp_path)
    seen = []
    adapter.progress_callback = seen.append
    proc = adapter._run_subprocess(
        adapter._build_argv("prompt", str(tmp_path), ""), str(tmp_path)
    )
    assert proc.returncode == 0
    assert f"{tmp_path} False None 1" in seen[0]
    assert os.environ["SNODO_JOB_ID"] == "j_private"  # parent remains untouched
    assert os.environ["SNODO_PROJECT_ROOT"] == str(tmp_path / "real-project")
    assert "SNODO_CODER_SUBPROCESS" not in os.environ
    assert adapter.last_usage["input_tokens"] == 2
    assert adapter._resolve_binary_path() == str(fake_cli)
