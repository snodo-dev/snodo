"""Tests for the recon append-only event log."""

from concurrent.futures import ThreadPoolExecutor

from snodo.recon.event_log import ReconEventLog


def test_append_read_and_offset(tmp_path):
    log = ReconEventLog(tmp_path)
    log.append(0, "reader", "model-a", "start")
    first, offset = log.read()
    assert len(first) == 1
    log.append(1, "reader", "model-b", "finish")
    second, new_offset = log.read(offset)
    assert [e["kind"] for e in second] == ["finish"]
    assert new_offset > offset


def test_concurrent_appends_are_whole_lines(tmp_path):
    log = ReconEventLog(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: log.append(n, "agent", "model", "start", {"n": n}), range(100)))
    assert len(log.path.read_text().splitlines()) == 100
    assert len(log.read()[0]) == 100


def test_partial_tail_is_left_for_next_read(tmp_path):
    log = ReconEventLog(tmp_path)
    log.path.write_bytes(b'{"kind":"start"}\n{"kind":')
    events, offset = log.read()
    assert events == [{"kind": "start"}]
    assert offset == len(b'{"kind":"start"}\n')


def test_missing_file_and_lazy_creation(tmp_path):
    log = ReconEventLog(tmp_path / "missing")
    assert log.read() == ([], 0)
    assert not log.path.exists()


def test_format_each_kind_and_unknown():
    base = {"timestamp": "now", "lane": 2, "agent": "a", "model": "m", "detail": {}}
    for kind in ("start", "tool_turn", "failover", "finish", "failure", "future_kind"):
        line = ReconEventLog.format({**base, "kind": kind})
        assert "lane 2" in line and "\n" not in line
    assert "read_file" in ReconEventLog.format({**base, "kind": "tool_turn", "detail": {"tools": ["read_file"]}})
    assert "future_kind" in ReconEventLog.format({**base, "kind": "future_kind"})


def test_argument_summaries_are_bounded_and_do_not_dump_structures():
    summary = ReconEventLog.summarize_arguments({"path": "x" * 1000, "data": {"secret": "large"}})
    assert len(summary["path"]) <= 160
    assert summary["data"] == "<dict>"


def test_write_failure_is_swallowed(tmp_path, monkeypatch):
    log = ReconEventLog(tmp_path)
    from pathlib import Path

    monkeypatch.setattr(Path, "open", lambda *a, **k: (_ for _ in ()).throw(OSError("nope")))
    log.append(0, "a", "m", "failure")
