"""Tests for the read-only dashboard Gates route."""

from datetime import datetime, timezone
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from snodo.web import routes_gates


def test_builder_combines_and_tags_project_records(monkeypatch) -> None:
    records = {
        "/one": [
            {"task": "a", "plan": "p", "validator": "v", "outcome": "landed_after_retry", "when": "2026-11-13T00:00:00+00:00"},
        ],
        "/two": [
            {"task": "b", "plan": "q", "validator": "v", "outcome": "not_landed", "when": "2026-11-14T00:00:00+00:00"},
        ],
    }
    monkeypatch.setattr(routes_gates, "gate_records", lambda path, _days, _now: records[str(path)])
    result = routes_gates.build_gates(
        [SimpleNamespace(name="one", path="/one"), SimpleNamespace(name="two", path="/two")],
        21,
        datetime(2026, 11, 14, tzinfo=timezone.utc),
    )
    assert [row["project"] for row in result["records"]] == ["two", "one"]
    assert result["sent_back"] == 2
    assert result["landed_after_retry"] == 1
    assert result["by_validator"] == [{"validator": "v", "count": 2}]
    assert result["by_project"] == [
        {"name": "one", "sent_back": 1, "landed_after_retry": 1},
        {"name": "two", "sent_back": 1, "landed_after_retry": 0},
    ]


def test_builder_limits_records_to_100(monkeypatch) -> None:
    monkeypatch.setattr(routes_gates, "gate_records", lambda *_args: [
        {"task": str(index), "plan": None, "validator": None, "outcome": "not_landed", "when": f"2026-11-{index % 30 + 1:02d}T00:00:00+00:00"}
        for index in range(101)
    ])
    result = routes_gates.build_gates([SimpleNamespace(name="one", path="/one")], 21)
    assert len(result["records"]) == 100


def test_handler_returns_json_and_rejects_invalid_days() -> None:
    app = Starlette(routes=[Route("/gates", routes_gates.handler, methods=["GET"])])
    app.state.projects = []
    client = TestClient(app)
    response = client.get("/gates")
    assert response.status_code == 200
    assert response.json()["window_days"] == 21
    assert response.headers["content-type"].startswith("application/json")
    assert client.get("/gates?days=3").status_code == 400


def test_handler_collects_in_worker_thread(monkeypatch) -> None:
    import threading

    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.requests import Request

    loop_thread = None
    collection_thread = None

    async def middleware(request: Request, call_next):
        nonlocal loop_thread
        loop_thread = threading.get_ident()
        return await call_next(request)

    def collect(*_args):
        nonlocal collection_thread
        collection_thread = threading.get_ident()
        return {"unchanged": True}

    monkeypatch.setattr(routes_gates, "build_gates", collect)
    app = Starlette(routes=[Route("/gates", routes_gates.handler, methods=["GET"])])
    app.add_middleware(BaseHTTPMiddleware, dispatch=middleware)
    app.state.projects = []

    response = TestClient(app).get("/gates")

    assert response.status_code == 200
    assert response.json() == {"unchanged": True}
    assert collection_thread != loop_thread
