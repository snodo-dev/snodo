import json
import socket
from concurrent.futures import ThreadPoolExecutor

import psutil
import pytest

from snodo.infrastructure import serve_registry as registry


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path / ".snodo"))
    return tmp_path / ".snodo" / "serve_registry.json"


def test_recording_replaces_same_project_and_mode_but_keeps_others():
    registry.record_serve_process("/one", "dev", 10)
    newer = registry.record_serve_process("/one", "dev", 12)
    registry.record_serve_process("/one", "prod", 20)
    registry.record_serve_process("/two", "dev", 30)

    entries = registry.list_serve_processes()
    assert len(entries) == 3
    assert newer in entries
    assert {(entry["project"], entry["mode"]) for entry in entries} == {
        ("/one", "dev"), ("/one", "prod"), ("/two", "dev")
    }
    assert next(entry for entry in entries if entry["project"] == "/one" and entry["mode"] == "dev")["port"] == 12


def test_removing_record_leaves_newer_replacement():
    old = registry.record_serve_process("/one", "dev", 10)
    newer = registry.record_serve_process("/one", "dev", 12)

    registry.remove_serve_process(old)

    assert registry.list_serve_processes() == [newer]


def test_listing_live_entries_does_not_rewrite_file(home):
    home.parent.mkdir(parents=True)
    current = psutil.Process()
    live = {
        "project": "/one", "mode": "dev", "port": 10, "hostname": "",
        "pid": current.pid, "started_at": current.create_time(), "host": socket.gethostname(),
    }
    contents = json.dumps([live])
    home.write_text(contents, encoding="utf-8")
    original_stat = home.stat()

    for _ in range(3):
        assert registry.list_serve_processes() == [live]

    assert home.read_text(encoding="utf-8") == contents
    assert home.stat().st_mtime_ns == original_stat.st_mtime_ns


def test_listing_drops_gone_and_reused_processes_and_prunes_file(home):
    home.parent.mkdir(parents=True)
    current = psutil.Process()
    live = {
        "project": "/one", "mode": "dev", "port": 10, "hostname": "",
        "pid": current.pid, "started_at": current.create_time(), "host": socket.gethostname(),
    }
    stale = {**live, "pid": 99999999, "started_at": 1}
    reused = {**live, "started_at": 1}
    home.write_text(json.dumps([live, stale, reused]), encoding="utf-8")

    assert registry.list_serve_processes() == [live]
    assert json.loads(home.read_text(encoding="utf-8")) == [live]


def test_corrupt_file_reads_empty_and_recording_recovers(home):
    home.parent.mkdir(parents=True)
    home.write_text("{", encoding="utf-8")

    assert registry.list_serve_processes() == []
    recorded = registry.record_serve_process("/one", "dev", 10)

    assert registry.list_serve_processes() == [recorded]


def test_concurrent_writers_all_recorded():
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: registry.record_serve_process(f"/{n}", "dev", n), range(8)))
    assert len(registry.list_serve_processes()) == 8


def test_recording_ws_transport_round_trips():
    recorded = registry.record_serve_process("/one", "dev", 10, transport="ws")

    assert registry.list_serve_processes() == [recorded]
    assert recorded["transport"] == "ws"


def test_old_registry_entry_without_transport_reads_as_cloudflare(home):
    home.parent.mkdir(parents=True)
    current = psutil.Process()
    legacy = {
        "project": "/one", "mode": "dev", "port": 10, "hostname": "api.test",
        "pid": current.pid, "started_at": current.create_time(), "host": socket.gethostname(),
    }
    home.write_text(json.dumps([legacy]), encoding="utf-8")

    entry = registry.list_serve_processes()[0]
    assert entry.get("transport", "cloudflare") == "cloudflare"
