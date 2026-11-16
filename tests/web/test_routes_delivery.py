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

    assert [
        {key: row[key] for key in ("name", "landed", "plans_in_main", "median_flow_seconds", "first_try_rate", "flow_load")}
        for row in result["projects"]
    ] == [
        {"name": "one", "landed": 2, "plans_in_main": 1, "median_flow_seconds": 10,
         "first_try_rate": 1.0, "flow_load": 3},
        {"name": "two", "landed": 1, "plans_in_main": 1, "median_flow_seconds": None,
         "first_try_rate": 0.5, "flow_load": 4},
    ]
    assert result["by_day"][-1] == {"date": "2026-11-14", "landed": 3}
    assert {key: result["totals"][key] for key in (
        "landed", "flow_load", "plans_not_in_main", "median_flow_seconds", "first_try_rate"
    )} == {
        "landed": 3, "flow_load": 7, "plans_not_in_main": 1,
        "median_flow_seconds": None, "first_try_rate": 0.75,
    }
    assert result["totals"]["unclassified"] == 0
    assert result["totals"]["by_type"]["feature"]["landed"] == 0


def test_delivery_efficiency_is_null_when_no_tasks_have_known_history(monkeypatch) -> None:
    metrics = {
        "landed": 1, "landed_by_day": [], "median_flow_seconds": None,
        "first_try_rate": None, "flow_load": 0, "efficiency": None,
        "efficiency_tasks": 0, "_efficiency_flow_seconds": 0.0,
        "_efficiency_rework_seconds": 0.0,
    }
    monkeypatch.setattr(routes_delivery, "flow_metrics", lambda *_args: metrics)
    monkeypatch.setattr(routes_delivery, "plans_delivery", lambda _path: {})

    result = routes_delivery.build_delivery(
        [SimpleNamespace(name="project", path="/project")],
        7, datetime(2026, 11, 14, tzinfo=timezone.utc),
    )

    assert result["projects"][0]["efficiency"] is None
    assert result["projects"][0]["efficiency_tasks"] == 0
    assert result["totals"]["efficiency"] is None
    assert result["totals"]["efficiency_tasks"] == 0


def test_handler_accepts_days_30_and_returns_json() -> None:
    app = Starlette(routes=[Route("/delivery", routes_delivery.handler, methods=["GET"])])
    app.state.projects = []
    client = TestClient(app)

    response = client.get("/delivery?days=30")

    assert response.status_code == 200
    assert response.json()["window_days"] == 30
    assert response.headers["content-type"].startswith("application/json")
    assert client.get("/delivery?days=90").status_code == 200


def test_handler_rejects_unsupported_days() -> None:
    app = Starlette(routes=[Route("/delivery", routes_delivery.handler, methods=["GET"])])
    app.state.projects = []

    client = TestClient(app)
    assert client.get("/delivery?days=5").status_code == 400
    assert client.get("/delivery?days=45").status_code == 400


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


def test_totals_pool_type_durations_and_weekly_counts(monkeypatch) -> None:
    projects = [SimpleNamespace(name=name, path=f"/{name}") for name in ("one", "two")]
    metrics = {
        "/one": {"landed": 2, "landed_by_day": [], "median_flow_seconds": 10,
                 "first_try_rate": None, "flow_load": 0,
                 "by_type": {"feature": {"landed": 1}, "defect": {"landed": 1}},
                 "unclassified": 1, "by_week": [{"week_start": "2026-11-09", "feature": 1, "defect": 1, "debt": 0, "risk": 0, "unclassified": 1}],
                 "_flow_seconds": [10, 30], "_flow_seconds_by_type": {"feature": [10], "defect": [30]}},
        "/two": {"landed": 1, "landed_by_day": [], "median_flow_seconds": 50,
                 "first_try_rate": None, "flow_load": 0,
                 "by_type": {"feature": {"landed": 1}}, "unclassified": 0,
                 "by_week": [{"week_start": "2026-11-09", "feature": 1, "defect": 0, "debt": 0, "risk": 0, "unclassified": 0}],
                 "_flow_seconds": [50], "_flow_seconds_by_type": {"feature": [50]}},
    }
    monkeypatch.setattr(routes_delivery, "flow_metrics", lambda path, *_: metrics[str(path)])
    monkeypatch.setattr(routes_delivery, "plans_delivery", lambda *_: {})
    result = routes_delivery.build_delivery(projects, 7, datetime(2026, 11, 14, tzinfo=timezone.utc))
    totals = result["totals"]
    assert totals["by_type"]["feature"]["landed"] == 2
    assert totals["by_type"]["feature"]["median_flow_seconds"] == 30
    assert totals["by_type"]["defect"]["median_flow_seconds"] == 30
    assert totals["unclassified"] == 1
    assert totals["by_week"][-1] == {"week_start": "2026-11-09", "feature": 2, "defect": 1, "debt": 0, "risk": 0, "unclassified": 1}


def test_efficiency_totals_pool_seconds_not_project_ratios(monkeypatch) -> None:
    projects = [SimpleNamespace(name=name, path=f"/{name}") for name in ("one", "two")]
    metrics = {
        "/one": {"landed": 1, "landed_by_day": [], "median_flow_seconds": None, "first_try_rate": None, "flow_load": 0, "efficiency": 0.5, "efficiency_tasks": 1, "_efficiency_flow_seconds": 100.0, "_efficiency_rework_seconds": 50.0},
        "/two": {"landed": 1, "landed_by_day": [], "median_flow_seconds": None, "first_try_rate": None, "flow_load": 0, "efficiency": 0.9, "efficiency_tasks": 1, "_efficiency_flow_seconds": 900.0, "_efficiency_rework_seconds": 90.0},
    }
    monkeypatch.setattr(routes_delivery, "flow_metrics", lambda path, *_: metrics[str(path)])
    monkeypatch.setattr(routes_delivery, "plans_delivery", lambda *_: {})
    result = routes_delivery.build_delivery(projects, 7, datetime(2026, 11, 14, tzinfo=timezone.utc))
    assert [row["efficiency"] for row in result["projects"]] == [0.5, 0.9]
    assert result["totals"]["efficiency"] == 0.86
    assert result["totals"]["efficiency_tasks"] == 2
