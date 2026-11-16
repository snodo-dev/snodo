"""Tests for audit-derived task flow types."""

import json
from pathlib import Path

from snodo.web import flow_types


def _event(task: str, kind: str, timestamp: str) -> str:
    return json.dumps({"event_type": "task_classified", "timestamp": timestamp,
                       "data": {"task_ref": task, "flow_type": kind,
                                "wave_id": "w1", "task_summary": "task"}}) + "\n"


def test_merges_and_chooses_latest_event(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path.parent / "flow-type-cache"))
    audit = tmp_path / ".snodo"
    audit.mkdir()
    (audit / "audit.log-2026-w35").write_text(
        _event("a", "feature", "2026-01-01T00:00:00Z") + _event("b", "risk", "2026-01-03T00:00:00Z"))
    (audit / "audit.log").write_text(
        _event("a", "defect", "2026-01-02T00:00:00Z") + _event("c", "unknown", "2026-01-04T00:00:00Z") + "{bad\n")
    assert flow_types.task_flow_types(tmp_path) == {"a": "defect", "b": "risk"}


def test_missing_audit_folder(tmp_path: Path) -> None:
    assert flow_types.task_flow_types(tmp_path) == {}


def test_unchanged_rotated_file_not_reopened_and_size_invalidates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path.parent / "flow-type-cache"))
    audit = tmp_path / ".snodo"
    audit.mkdir()
    rotated = audit / "audit.log.2026-w35"
    rotated.write_text(_event("a", "feature", "2026-01-01T00:00:00Z"))
    original = Path.open
    opened: list[Path] = []

    def counting_open(path: Path, *args, **kwargs):
        if path == rotated:
            opened.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counting_open)
    flow_types.task_flow_types(tmp_path)
    flow_types.task_flow_types(tmp_path)
    assert len(opened) == 1
    monkeypatch.setattr(Path, "open", original)
    rotated.write_text(_event("a", "debt", "2026-01-01T00:00:00Z") + " ")
    monkeypatch.setattr(Path, "open", counting_open)
    assert flow_types.task_flow_types(tmp_path)["a"] == "debt"
    assert len(opened) == 2
    assert tmp_path not in flow_types._cache_path(tmp_path).parents


def test_unwritable_cache_is_nonfatal(tmp_path: Path, monkeypatch) -> None:
    audit = tmp_path / ".snodo"
    audit.mkdir()
    (audit / "audit.log.x").write_text(_event("a", "feature", "2026-01-01T00:00:00Z"))
    monkeypatch.setattr(flow_types, "_cache_path", lambda _project: tmp_path / "blocked" / "cache.json")
    (tmp_path / "blocked").mkdir()
    (tmp_path / "blocked").chmod(0o400)
    try:
        assert flow_types.task_flow_types(tmp_path) == {"a": "feature"}
    finally:
        (tmp_path / "blocked").chmod(0o700)
