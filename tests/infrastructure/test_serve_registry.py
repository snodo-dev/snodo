import json
import os
from concurrent.futures import ThreadPoolExecutor

import pytest

from snodo.infrastructure import serve_registry as registry


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("SNODO_HOME", str(tmp_path / ".snodo"))
    monkeypatch.setattr(registry, "_path", lambda: tmp_path / ".snodo" / "serve_registry.json")


def test_write_list_remove_and_replacement(monkeypatch):
    first = registry.record_serve_process("/one", "dev", 10)
    assert registry.list_serve_processes()[0]["project"] == "/one"
    registry.remove_serve_process(first)
    assert registry.list_serve_processes() == []
    one = registry.record_serve_process("/one", "dev", 10)
    registry.record_serve_process("/two", "dev", 11)
    newer = {**one, "port": 12}
    monkeypatch.setattr(registry, "record_serve_process", lambda *a: None)
    registry._path().write_text(json.dumps([one, {**one, "port": 12}, {"project": "/two", "mode": "dev", "port": 11, "hostname": "", "pid": os.getpid(), "started_at": __import__('psutil').Process().create_time(), "host": __import__('socket').gethostname()}]))
    assert len(registry.list_serve_processes()) == 3
    assert newer["port"] == 12


def test_dead_reused_pid_and_corrupt(monkeypatch):
    registry._path().parent.mkdir(parents=True)
    dead = {"pid": 99999999, "started_at": 1, "host": "host"}
    registry._path().write_text(json.dumps([dead]))
    assert registry.list_serve_processes() == []
    entry = {"pid": os.getpid(), "started_at": 1, "host": __import__('socket').gethostname()}
    registry._path().write_text(json.dumps([entry]))
    monkeypatch.setattr(registry, "process_identity_matches", lambda *a: False)
    assert registry.list_serve_processes() == []
    registry._path().write_text("{")
    assert registry.list_serve_processes() == []


def test_concurrent_writers_all_recorded():
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: registry.record_serve_process(f"/{n}", "dev", n), range(8)))
    assert len(registry.list_serve_processes()) == 8
