"""Mock and benchmark run cloud-delivery exclusions."""

from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from snodo.infrastructure import cloud_sync
from snodo.infrastructure import cloud_liveness


def _audit(root: Path, coder: str, session_id: str = "session") -> SimpleNamespace:
    return SimpleNamespace(
        log_path=root / ".snodo" / "audit.log",
        events=[
            SimpleNamespace(
                sequence=1, event_type="session_started",
                data={"session_id": "previous"},
            ),
            SimpleNamespace(
                sequence=2, event_type="task_complete",
                data={"usage": [{"role": "coder", "coder": "mock"}]},
            ),
            SimpleNamespace(
                sequence=3, event_type="session_started",
                data={"session_id": session_id},
            ),
            SimpleNamespace(
                sequence=4, event_type="task_complete",
                data={"session_id": session_id,
                      "usage": [{"role": "coder", "coder": coder}]},
            ),
        ],
    )


def _config() -> dict:
    return {"cloud": {"sync_enabled": True, "api_key": "sndo_live_test"}}


def test_mock_coder_run_skips_cloud_sync(tmp_path):
    (tmp_path / ".snodo").mkdir()
    audit = _audit(tmp_path, "mock")
    with patch.object(cloud_sync.CloudSyncDispatcher, "sync") as sync:
        cloud_sync.sync_if_enabled("session", str(tmp_path), audit, _config())
    sync.assert_not_called()
    assert audit.log_path.parent == tmp_path / ".snodo"


def test_real_coder_run_still_syncs(tmp_path):
    (tmp_path / ".snodo").mkdir()
    audit = _audit(tmp_path, "litellm")
    finished = Event()

    def sync(*args, **kwargs):
        finished.set()
        return {"synced": 1, "failed": False, "pending": 0}

    with patch.object(cloud_sync.CloudSyncDispatcher, "sync", side_effect=sync) as dispatch:
        cloud_sync.sync_if_enabled("session", str(tmp_path), audit, _config())
        assert finished.wait(2)
    dispatch.assert_called_once()


def test_historical_mock_run_does_not_suppress_real_sync(tmp_path):
    (tmp_path / ".snodo").mkdir()
    audit = _audit(tmp_path, "litellm")
    finished = Event()
    with patch.object(
        cloud_sync.CloudSyncDispatcher, "sync",
        side_effect=lambda *args, **kwargs: finished.set(),
    ) as dispatch:
        cloud_sync.sync_if_enabled("session", str(tmp_path), audit, _config())
        assert finished.wait(2)
    dispatch.assert_called_once()


def test_benchmark_run_skips_cloud_sync(tmp_path, monkeypatch):
    (tmp_path / ".snodo").mkdir()
    audit = _audit(tmp_path, "litellm")
    monkeypatch.setenv("SNODO_BENCHMARK", "1")
    with patch.object(cloud_sync.CloudSyncDispatcher, "sync") as sync:
        cloud_sync.sync_if_enabled("session", str(tmp_path), audit, _config())
    sync.assert_not_called()
    with patch.object(cloud_liveness, "_deliver") as liveness:
        assert not cloud_liveness.request_liveness_push(
            "session", str(tmp_path), config=_config(),
        )
    liveness.assert_not_called()


def test_mock_audit_event_suppresses_liveness(tmp_path, monkeypatch):
    (tmp_path / ".snodo").mkdir()
    audit = _audit(tmp_path, "mock")
    monkeypatch.setattr(cloud_liveness, "_ARMED", True)
    event = SimpleNamespace(
        event_type="dispatch",
        data={"session_id": "session", "coder": "mock"},
    )
    with patch.object(cloud_liveness, "request_liveness_push") as liveness:
        cloud_liveness._on_audit_event(event, audit)
    liveness.assert_not_called()


def test_historical_mock_run_does_not_suppress_real_liveness(tmp_path, monkeypatch):
    (tmp_path / ".snodo").mkdir()
    audit = _audit(tmp_path, "litellm")
    monkeypatch.setattr(cloud_liveness, "_ARMED", True)
    event = SimpleNamespace(event_type="dispatch", data={"session_id": "session"})
    with patch.object(cloud_liveness, "request_liveness_push") as liveness:
        cloud_liveness._on_audit_event(event, audit)
    liveness.assert_called_once_with("session", str(tmp_path), force=False)
