"""Tests for the read-only dashboard Delivery route."""

from datetime import datetime, timezone
from types import SimpleNamespace

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from snodo.web import routes_delivery


def test_builder_aggregates_project_metrics(monkeypatch) -> None:
    data = {
        "/one": {"landed": 2, "landed_by_day": [{"date": "2026-11-14", "landed": 2}],
                 "median_flow_seconds": 10, "first_try_rate": 1.0, "flow_load": 3},
        "/two": {"landed": 1, "landed_by_day": [{"date": "2026-11-14", "landed": 1}],
                 "median_flow_seconds": None, "first_try_rate": 0.5, "flow_load": 4},
    }
    deliveries = {"/one": {"a": "in_main", "b": "not_in_main"}, "/two": {"c": "in_main"}}
    monkeypatch.setattr(routes_delivery, "flow_metrics", lambda path, _days, _now: data[str(path)])
    monkeypatch.setattr(routes_delivery, "plans_delivery", lambda path: deliveries[str(path)])
    projects = [SimpleNamespace(name="one", path="/one"), SimpleNamespace(name="two", path="/two")]

    result = routes_delivery.build_delivery(
        projects, 7, datetime(2026, 11, 14, tzinfo=timezone.utc)
    )

    assert result["projects"] == [
        {"name": "one", "landed": 2, "plans_in_main": 1, "median_flow_seconds": 10,
         "first_try_rate": 1.0, "flow_load": 3},
        {"name": "two", "landed": 1, "plans_in_main": 1, "median_flow_seconds": None,
         "first_try_rate": 0.5, "flow_load": 4},
    ]
    assert result["by_day"][-1] == {"date": "2026-11-14", "landed": 3}
    assert result["totals"] == {
        "landed": 3, "flow_load": 7, "plans_not_in_main": 1,
        "median_flow_seconds": None, "first_try_rate": 0.75,
    }


def test_handler_accepts_days_30_and_returns_json() -> None:
    app = Starlette(routes=[Route("/delivery", routes_delivery.handler, methods=["GET"])])
    app.state.projects = []
    client = TestClient(app)

    response = client.get("/delivery?days=30")

    assert response.status_code == 200
    assert response.json()["window_days"] == 30
    assert response.headers["content-type"].startswith("application/json")


def test_handler_rejects_unsupported_days() -> None:
    app = Starlette(routes=[Route("/delivery", routes_delivery.handler, methods=["GET"])])
    app.state.projects = []

    assert TestClient(app).get("/delivery?days=5").status_code == 400


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

    monkeypatch.setattr(routes_delivery, "build_delivery", collect)
    app = Starlette(routes=[Route("/delivery", routes_delivery.handler, methods=["GET"])])
    app.add_middleware(BaseHTTPMiddleware, dispatch=middleware)
    app.state.projects = []

    response = TestClient(app).get("/delivery")

    assert response.status_code == 200
    assert response.json() == {"unchanged": True}
    assert collection_thread != loop_thread
