from unittest.mock import patch

from snodo.infrastructure.audit import AuditLog
from snodo.infrastructure.cloud_unsent import events_after_cursor


def test_events_after_cursor_streams_archive_before_current_events(tmp_path):
    path = tmp_path / "audit.log"
    archived = AuditLog(str(path))
    archived.append_event("first", {})
    archived.append_event("second", {})
    (tmp_path / "audit.log.2026-w41").write_bytes(path.read_bytes())
    path.write_text("")
    current = AuditLog(str(path))
    current.append_event("third", {})

    assert [event.sequence for event in events_after_cursor(current, 0)] == [1, 2]


def test_events_after_cursor_skips_archives_when_cursor_is_in_current_file(tmp_path):
    path = tmp_path / "audit.log"
    archived = AuditLog(str(path))
    archived.append_event("archived", {})
    (tmp_path / "audit.log.2026-w41").write_bytes(path.read_bytes())
    path.write_text("")
    current = AuditLog(str(path))
    current.append_event("current", {})

    with patch.object(current, "iter_history", wraps=current.iter_history) as history:
        assert list(events_after_cursor(current, 1)) == []
        history.assert_not_called()


def test_events_after_cursor_uses_loaded_events_without_archives(tmp_path):
    path = tmp_path / "audit.log"
    current = AuditLog(str(path))
    current.append_event("first", {})
    current.append_event("second", {})

    with patch.object(current, "iter_history", wraps=current.iter_history) as history:
        assert [event.sequence for event in events_after_cursor(current, 0)] == [1]
        history.assert_not_called()
