"""Safe, opt-in request/response diagnostics for MCP traffic."""

import asyncio
from types import SimpleNamespace

from snodo.mcp.transport import (
    _MCP_LOG_MAX_CHARS,
    _enable_mcp_traffic_logging,
    _safe_mcp_excerpt,
)


class _Responder:
    request_id = 42

    async def respond(self, response):
        self.sent = response


class _Response:
    def model_dump(self, **_kwargs):
        return {"content": [{"text": "done"}]}


def test_verbose_logs_one_redacted_truncated_line_per_request_and_response(capsys):
    class Dispatcher:
        async def _handle_request(self, message, req, *_args):
            await message.respond(_Response())

        async def _handle_notification(self, _notification):
            return None

    fake_mcp = SimpleNamespace(_mcp_server=Dispatcher())
    _enable_mcp_traffic_logging(fake_mcp)
    params = SimpleNamespace(
        name="read_file",
        arguments={
            "path": "large.txt",
            "authorization": "Bearer bearer-secret-value-123",
            "CF-Access-Client-Secret": "service-secret-value-456",
            "content": "x" * (_MCP_LOG_MAX_CHARS * 3),
        },
        model_dump=lambda **_kwargs: {
            "name": "read_file",
            "arguments": {
                "path": "large.txt",
                "authorization": "Bearer bearer-secret-value-123",
                "CF-Access-Client-Secret": "service-secret-value-456",
                "content": "x" * (_MCP_LOG_MAX_CHARS * 3),
            },
        },
    )
    req = SimpleNamespace(method="tools/call", params=params)
    asyncio.run(fake_mcp._mcp_server._handle_request(
        _Responder(), req, None, None, False,
    ))

    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 2
    assert " IN tools/call tool=read_file id=42" in lines[0]
    assert " OUT tools/call tool=read_file id=42 duration=" in lines[1]
    assert " ok" in lines[1]
    assert "bearer-secret-value-123" not in "\n".join(lines)
    assert "service-secret-value-456" not in "\n".join(lines)
    assert "[redacted]" in lines[0]
    assert len(lines[0].split(" data=", 1)[1]) < _MCP_LOG_MAX_CHARS + 20


def test_safe_excerpt_caps_large_arguments_and_results():
    excerpt = _safe_mcp_excerpt({"result": "r" * 1000})
    assert len(excerpt) == _MCP_LOG_MAX_CHARS + 1
    assert excerpt.endswith("…")
