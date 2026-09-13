"""Failure-reason survival tests for the dashboard read layer.

FILE: tests/dashboard/test_failure_reason_survival.py

The dashboard shows an empty pane for "nothing to show" and "the state
file broke" identically. These tests induce each parse/open failure and
assert the reason reaches the log the operator would check — the safe
value must keep being returned, but it must stop being the whole story.
"""

import logging
from pathlib import Path

from snodo.dashboard.liveness import _iso_to_epoch
from snodo.dashboard.providers import DashboardDataProvider
from snodo.infrastructure.audit import AuditError


def _joined(caplog) -> str:
    return "\n".join(r.getMessage() for r in caplog.records)


def _provider(tmp_path: Path) -> DashboardDataProvider:
    (tmp_path / ".snodo").mkdir()
    return DashboardDataProvider(str(tmp_path))


class TestWaveFiles:
    def test_corrupt_wave_list_logs_parse_reason(self, tmp_path, caplog):
        provider = _provider(tmp_path)
        (tmp_path / ".snodo" / "wave.json").write_text('{"waves": [oops')

        with caplog.at_level(logging.WARNING, logger="snodo.dashboard.providers"):
            assert provider.get_waves("s1") == []

        assert "wave.json" in _joined(caplog)
        assert "JSONDecodeError" in _joined(caplog)

    def test_corrupt_wave_detail_logs_parse_reason(self, tmp_path, caplog):
        provider = _provider(tmp_path)
        (tmp_path / ".snodo" / "wave.json").write_text('not json at all')

        with caplog.at_level(logging.WARNING, logger="snodo.dashboard.providers"):
            assert provider.get_wave_detail("w1") is None

        assert "w1" in _joined(caplog)
        assert "JSONDecodeError" in _joined(caplog)


class TestAuditLogLoad:
    def test_audit_open_failure_logs_reason(self, tmp_path, caplog, monkeypatch):
        provider = _provider(tmp_path)
        (tmp_path / ".snodo" / "audit.log").write_text("x")

        class _FailingAuditLog:
            def __init__(self, *args, **kwargs):
                raise AuditError("audit.log is locked by another writer")

        monkeypatch.setattr(
            "snodo.dashboard.providers.AuditLog", _FailingAuditLog
        )
        with caplog.at_level(logging.WARNING, logger="snodo.dashboard.providers"):
            assert provider._get_audit_log() is None

        assert "audit.log" in _joined(caplog)
        assert "locked by another writer" in _joined(caplog)


class TestAgentMemory:
    def test_agent_count_failure_logs_reason(self, tmp_path, caplog, monkeypatch):
        provider = _provider(tmp_path)

        class _FailingManager:
            def list_agents(self):
                raise RuntimeError("memory store index corrupt")

        monkeypatch.setattr(
            "snodo.infrastructure.memory.AgentMemoryManager", _FailingManager
        )
        with caplog.at_level(logging.DEBUG, logger="snodo.dashboard.providers"):
            assert provider._count_agents_for_mode("build") == 0
            assert provider._get_agents_for_mode("build") == []

        assert "Could not count agents" in _joined(caplog)
        assert "memory store index corrupt" in _joined(caplog)
        assert "Could not list agents" in _joined(caplog)


class TestTimestampParsing:
    def test_bad_timestamp_logs_the_offending_value(self, caplog):
        with caplog.at_level(logging.DEBUG, logger="snodo.dashboard.liveness"):
            assert _iso_to_epoch("tomorrow-ish") is None

        assert "unparseable timestamp" in _joined(caplog)
        assert "tomorrow-ish" in _joined(caplog)
        assert "ValueError" in _joined(caplog)
