from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from snodo.validators.tool_registry import ValidatorToolContext, ValidatorToolRegistry
from snodo_validator_tool_jev import tool


@pytest.fixture
def context(tmp_path):
    return ValidatorToolContext(
        workspace_root=tmp_path,
        spec_text="task spec",
        validator_id="review",
        tool_options={"max_input_chars": 100},
        diff_refs=("base", "head"),
        diff_accessor=lambda *refs: f"diff {refs}",
    )


def test_discovery_request_and_render(monkeypatch, context):
    plugin = SimpleNamespace(name="decide", load=lambda: tool)
    monkeypatch.setattr("importlib.metadata.entry_points", lambda *, group: [plugin])
    registry = ValidatorToolRegistry()
    registry.discover_plugins()
    registered = registry.lookup("decide")
    assert registered is tool
    assert registered.name == "decide"
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret-value")
    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "model": "jev-latest",
                "answers": {
                    "safe": {"noul": 0.9},
                    "kind": {"choice": "good", "probabilities": {"good": 0.8}, "confidence": 0.8},
                    "quality": {"score": "high", "legend": ["low", "high"], "probabilities": [0.1, 0.9], "confidence": 0.9},
                },
                "usage": {"input_tokens": 11, "output_tokens": 4},
            }

    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr("snodo_validator_tool_jev.httpx.post", post)
    args = {
        "context": "diff",
        "questions": [
            {"name": "safe", "type": "noul", "instructions": "Is safe?"},
            {"name": "kind", "type": "choice", "instructions": "Choose", "criteria": {"good": "Good", "bad": None}},
            {"name": "quality", "type": "score", "instructions": "Rate", "criteria": ["low", "high"]},
        ],
    }
    result = registered.handler(args, context)
    request = captured["json"]
    assert captured["url"] == "https://api.typesafe.ai/v1/systemone"
    assert captured["headers"]["Authorization"] == "Bearer secret-value"
    assert request["state"] == "diff ('base', 'head')"
    assert request["questions"]["safe"] == {"type": "noul", "instructions": "Is safe?"}
    assert request["questions"]["kind"]["criteria"] == {"good": "Good", "bad": None}
    assert request["questions"]["quality"]["criteria"] == ["low", "high"]
    assert "safe [noul]: 0.9" in result
    assert "kind [choice]: good" in result and "confidence=0.8" in result
    assert "quality [score]: high" in result and "probabilities=[0.1,0.9]" in result
    assert "input_tokens=11" in result and "output_tokens=4" in result
    assert "secret-value" not in result


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("missing", "TYPESAFE_API_KEY is not set"),
        ("http", "HTTP 503"),
        ("timeout", "request timed out"),
    ],
)
def test_readable_failures(monkeypatch, context, failure, message):
    args = {"context": "spec", "questions": [{"name": "q", "type": "noul", "instructions": "Check"}]}
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    if failure == "missing":
        result = tool.handler(args, context)
    else:
        monkeypatch.setenv("TYPESAFE_API_KEY", "never-print-this")
        if failure == "timeout":
            def post(*args, **kwargs):
                raise httpx.TimeoutException("never-print-this")
        else:
            class Response:
                status_code = 503

                def raise_for_status(self):
                    request = httpx.Request("POST", "https://example.test")
                    response = httpx.Response(503, request=request)
                    raise httpx.HTTPStatusError("failure", request=request, response=response)

            def post(*args, **kwargs):
                return Response()
        monkeypatch.setattr("snodo_validator_tool_jev.httpx.post", post)
        result = tool.handler(args, context)
    assert message in result
    assert "never-print-this" not in result


def test_truncates_context_and_confines_file_paths(monkeypatch, context, caplog):
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret-value")
    context = ValidatorToolContext(context.workspace_root, "x" * 101, "review", {"max_input_chars": 10})
    request_body = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"answers": {"q": {"noul": 0.5}}, "usage": {}}

    def post(url, **kwargs):
        request_body.update(kwargs["json"])
        return Response()

    monkeypatch.setattr("snodo_validator_tool_jev.httpx.post", post)
    args = {"context": "spec", "questions": [{"name": "q", "type": "noul", "instructions": "Check"}]}
    result = tool.handler(args, context)
    assert request_body["state"] == "x" * 10
    assert "context truncated" in result
    assert "secret-value" not in caplog.text

    context = ValidatorToolContext(context.workspace_root, context.spec_text, "review", {"max_input_chars": 100})
    path = context.workspace_root / "source.py"
    path.write_text("safe", encoding="utf-8")
    tool.handler(
        {"context": "files", "files": ["source.py"], "questions": args["questions"]}, context
    )
    assert request_body["state"] == "--- source.py ---\nsafe"
    escaped = Path("../outside.txt")
    assert "ValueError" in tool.handler(
        {"context": "files", "files": [str(escaped)], "questions": args["questions"]}, context
    )


def test_malformed_response_is_returned_as_tool_text(monkeypatch, context):
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret-value")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"answers": []}

    monkeypatch.setattr("snodo_validator_tool_jev.httpx.post", lambda *args, **kwargs: Response())
    result = tool.handler(
        {"context": "spec", "questions": [{"name": "q", "type": "noul", "instructions": "Check"}]},
        context,
    )
    assert "malformed response" in result
    assert "secret-value" not in result
