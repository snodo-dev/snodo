"""MCP progress notifications: asked-for calls stream narration, unasked do not.

FILE: tests/mcp/test_progress_notifications.py

A long-running tool call (validate_task) gives the caller nothing until it
returns, so an orchestrator cannot tell work-in-progress from a dead server.
MCP's answer is notifications/progress, gated by a progressToken the
CALLER supplies in the request's _meta. The pinned SDK (mcp 1.29.1) owns both
ends of that wire — the client stamps the token and routes notifications back
to a progress_callback; Context.report_progress emits only when a token was
supplied. These tests drive the REAL transport seam (in-memory client/server)
and hold the invariants:

- with a token: notifications arrive, carrying narration, while the call runs;
- without a token: the server emits NOTHING;
- progress is not a result: the final response is identical either way;
- a notification sink that raises never fails the call;
- no secrets, payloads or diff framing reach a notification body.

run_plan is deliberately absent: since #254 it starts the run as a job and
returns a job_id at once — a call that ends in a second has nothing to
report, and its narration lands in the job's stdout.log. No sink, no stream.
"""

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import anyio
import pytest
import yaml
from mcp import types
from mcp.server.fastmcp import Context
from mcp.shared.memory import create_connected_server_and_client_session
from snodo.compiler.models import Protocol
from snodo.mcp.server import ProtocolMCPServer
from snodo.mcp.transport import _sanitize_progress_line, build_fastmcp_server

# The quality validator is tooling-driven (echo test passed) — deterministic
# narration through the real runner, no LLM involved.
_PROTOCOL_DATA = {
    "protocol_id": "progress_test",
    "name": "Progress Test Protocol",
    "version": "1.0.0",
    "initial_mode": "producer",
    "modes": [
        {
            "mode_id": "producer",
            "name": "Producer",
            "tools": ["edit", "dispatch", "test", "validate"],
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


@pytest.fixture
def project_dir():
    d = tempfile.mkdtemp()
    subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=d, capture_output=True, check=True)
    (Path(d) / "README.md").write_text("test")
    snodo_dir = Path(d) / ".snodo"
    snodo_dir.mkdir()
    (snodo_dir / "protocol.yml").write_text(yaml.dump(_PROTOCOL_DATA))
    subprocess.run(["git", "add", "."], cwd=d, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=d, capture_output=True, check=True)
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _protocol_server(project_dir):
    return ProtocolMCPServer(Protocol(**_PROTOCOL_DATA), project_dir, mode_id="producer")


def _progress_text_types(msg) -> bool:
    return (
        isinstance(msg.root, types.ServerNotification)
        and isinstance(msg.root.root, types.ProgressNotification)
    )


class TestProgressRoundTrip:
    def test_call_with_token_streams_narration(self, project_dir):
        """A caller that supplies a progress token is shown the work in flight."""
        server = _protocol_server(project_dir)
        fastmcp = build_fastmcp_server(server)
        received: list = []

        async def on_progress(progress, total, message):
            received.append(message)

        async def run():
            async with create_connected_server_and_client_session(fastmcp) as session:
                return await session.call_tool(
                    "validate_task",
                    {"task_id": "t_stream", "task_spec": "do the thing"},
                    progress_callback=on_progress,
                )

        result = anyio.run(run)
        assert result.isError is False
        assert received, "expected progress notifications while the call ran"
        assert any("quality" in (m or "") for m in received), received
        # Bodies are narration: single-line, bounded.
        for m in received:
            assert m is None or "\n" not in m
            assert len(m or "") <= 241

    def test_call_without_token_emits_nothing(self, project_dir):
        """Unasked-for notifications are a bug: no token, no stream."""
        server = _protocol_server(project_dir)
        fastmcp = build_fastmcp_server(server)
        seen_notifications: list = []

        async def message_handler(msg):
            if _progress_text_types(msg):
                seen_notifications.append(msg)

        async def run():
            async with create_connected_server_and_client_session(
                fastmcp, message_handler=message_handler,
            ) as session:
                return await session.call_tool(
                    "validate_task",
                    {"task_id": "t_silent", "task_spec": "do the thing"},
                )

        result = anyio.run(run)
        assert result.isError is False
        assert seen_notifications == []

    def test_final_response_identical_with_and_without_progress(self, project_dir):
        """Progress is not a result: the same call returns the same response."""
        server = _protocol_server(project_dir)
        fastmcp = build_fastmcp_server(server)

        async def on_progress(progress, total, message):
            return None

        async def run():
            async with create_connected_server_and_client_session(fastmcp) as session:
                silent = await session.call_tool(
                    "validate_task", {"task_id": "t_eq", "task_spec": "same spec"},
                )
                narrated = await session.call_tool(
                    "validate_task",
                    {"task_id": "t_eq", "task_spec": "same spec"},
                    progress_callback=on_progress,
                )
            return silent, narrated

        silent, narrated = anyio.run(run)
        assert silent.content == narrated.content
        assert silent.isError == narrated.isError == False  # noqa: E712

    def test_tool_input_schema_unchanged_by_the_context_parameter(self, project_dir):
        """ctx is a server-side seam; the published tool schema must not gain it."""
        server = _protocol_server(project_dir)
        fastmcp = build_fastmcp_server(server)

        async def run():
            async with create_connected_server_and_client_session(fastmcp) as session:
                return await session.list_tools()

        tools = anyio.run(run)
        validate = next(t for t in tools.tools if t.name == "validate_task")
        assert "ctx" not in validate.inputSchema["properties"]

    def test_failing_notification_transport_does_not_fail_the_call(self, project_dir):
        """A sink that raises is an observer that died; the call must survive."""
        server = _protocol_server(project_dir)
        fastmcp = build_fastmcp_server(server)

        async def exploding_report_progress(self, progress, total=None, message=None):
            raise RuntimeError("notification transport wedged")

        received: list = []

        async def on_progress(progress, total, message):
            received.append(message)

        async def run():
            with patch.object(Context, "report_progress", exploding_report_progress):
                async with create_connected_server_and_client_session(fastmcp) as session:
                    return await session.call_tool(
                        "validate_task",
                        {"task_id": "t_raiser", "task_spec": "do the thing"},
                        progress_callback=on_progress,
                    )

        result = anyio.run(run)
        assert result.isError is False
        parsed = json.loads(result.content[0].text)
        assert parsed["status"] == "pass"


class TestProgressBodySanitizer:
    def test_jwt_and_api_keys_are_redacted(self):
        line = "auth failed for eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxIn0.abcDEF123"
        out = _sanitize_progress_line(line)
        assert "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9" not in out
        assert "[redacted]" in out

        out = _sanitize_progress_line("using key sk-proj-supersecretvalue123 for provider")
        assert "supersecretvalue123" not in out
        assert "[redacted]" in out

        out = _sanitize_progress_line("header Authorization: Bearer abcdefghijklMNOP1234=")
        assert "abcdefghijklMNOP1234=" not in out

    def test_diff_framing_lines_are_dropped(self):
        assert _sanitize_progress_line("diff --git a/src/x.py b/src/x.py") is None
        assert _sanitize_progress_line("@@ -1,3 +1,4 @@ def f()") is None
        assert _sanitize_progress_line("+++ b/src/x.py") is None

    def test_bodies_are_single_line_and_capped(self):
        assert _sanitize_progress_line("  \n  ") is None
        assert _sanitize_progress_line("") is None
        long_line = "turn 12: reading context " + ("x" * 1000)
        out = _sanitize_progress_line(long_line)
        assert out is not None
        assert len(out) <= 241
        assert "\n" not in out
        # narration is preserved, not just truncated at the front
        assert out.endswith("x" * 200 + "")

    def test_plain_narration_passes_through(self):
        assert _sanitize_progress_line("    quality: started") == "quality: started"
        assert _sanitize_progress_line("  [1.1_x] completed in 0.1s") == "[1.1_x] completed in 0.1s"
