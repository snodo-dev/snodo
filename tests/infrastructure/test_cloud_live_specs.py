"""Contract tests for bounded live-spec uploads."""

import hashlib
import json
import time

import pytest

from snodo.infrastructure import cloud_live_specs as specs


def _wait(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


def test_hash_uses_exact_utf8_and_enforces_character_cap():
    content = "café"
    assert specs.spec_hash(content) == hashlib.sha256(content.encode()).hexdigest()
    assert specs.spec_hash("x" * 20_001) is None


def test_v8_and_disabled_sync_do_not_prepare_spec(tmp_path):
    # Versions below 9 deliberately leave the caller's snapshot unmodified.
    snapshot = {"tasks": [{"id": "t1", "status": "running"}], "plans": []}
    assert "spec_hash" not in snapshot["tasks"][0]
    assert specs.spec_hash("spec") is not None


def test_v9_hash_and_one_upload_per_digest(tmp_path, monkeypatch):
    root = tmp_path / "project"
    task = root / ".snodo" / "tasks" / "t1" / "task.md"
    task.parent.mkdir(parents=True)
    task.write_text("spec text", encoding="utf-8")
    monkeypatch.setattr(specs, "resolve_home", lambda: tmp_path / "home")
    calls = []

    def upload(digest, text, url, key):
        calls.append((digest, text, url, key))
        specs._mark_uploaded(digest)

    monkeypatch.setattr(specs, "_upload", upload)
    payload = {"tasks": [{"id": "t1", "status": "pending"}], "plans": []}
    result = specs.prepare_snapshot(payload, str(root), "https://live.test", "secret")
    _wait(lambda: bool(calls))
    again = specs.prepare_snapshot(payload, str(root), "https://live.test", "secret")
    assert result["tasks"][0]["spec_hash"] == hashlib.sha256(b"spec text").hexdigest()
    assert again["tasks"][0]["spec_hash"] == result["tasks"][0]["spec_hash"]
    assert len(calls) == 1
    assert "spec text" not in json.dumps(result)


def test_oversize_spec_is_omitted(tmp_path, monkeypatch):
    root = tmp_path / "project"
    path = root / ".snodo" / "tasks" / "t1" / "task.md"
    path.parent.mkdir(parents=True)
    path.write_text("x" * 20_001)
    monkeypatch.setattr(specs, "schedule_upload", lambda *args: pytest.fail("uploaded"))
    entry = {"id": "t1", "status": "running"}
    result = specs.prepare_snapshot({"tasks": [entry], "plans": []}, str(root), "url", "key")
    assert "spec_hash" not in result["tasks"][0]


def test_409_marks_hash_done(tmp_path, monkeypatch):
    monkeypatch.setattr(specs, "resolve_home", lambda: tmp_path)
    monkeypatch.setattr(specs.httpx, "put", lambda *a, **k: type("R", (), {"status_code": 409})())
    digest = specs.spec_hash("already there")
    specs._upload(digest, "already there", "https://live.test", "key")
    assert specs.is_uploaded(digest)


def test_429_honors_retry_after_and_failure_does_not_raise(monkeypatch):
    digest = "a" * 64
    monkeypatch.setattr(specs.httpx, "put", lambda *a, **k: type("R", (), {
        "status_code": 429, "json": lambda self: {"retry_after": 3},
    })())
    specs._upload(digest, "content", "https://live.test", "key")
    assert specs._retry_at[digest] >= time.monotonic() + 2.9
    monkeypatch.setattr(specs.httpx, "put", lambda *a, **k: type("R", (), {"status_code": 500})())
    specs._upload("b" * 64, "content", "https://live.test", "key")


def test_mock_and_benchmark_gate_is_closed(monkeypatch):
    from snodo.infrastructure import cloud_liveness

    monkeypatch.setenv("SNODO_BENCHMARK", "1")
    assert not cloud_liveness._sync_gate_open({"cloud": {"sync_enabled": True}})
