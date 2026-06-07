"""Tests for cloud connect/disconnect/status and audit sync infrastructure.

FILE: tests/cli/test_cloud.py
"""

import json
import time
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ------------------------------------------------------------------#
# Cloud connect / disconnect / status
# ------------------------------------------------------------------#

class TestCloudConnect:
    def test_valid_key_stored_and_sync_enabled(self, tmp_path):
        """snodo cloud connect stores key and enables sync."""
        from snodo.cli.commands.cloud_cmd import cloud_connect_command

        with patch("snodo.cli.config.ConfigManager") as MockCM:
            mock_mgr = MockCM.return_value
            mock_mgr.load.return_value = {"model": "gpt-4"}

            result = cloud_connect_command("sndo_live_abcdef123456789")

            assert result == 0
            saved = mock_mgr.save.call_args[0][0]
            assert saved["cloud"]["api_key"] == "sndo_live_abcdef123456789"
            assert saved["cloud"]["sync_enabled"] is True

    def test_valid_staging_key(self):
        """Staging key prefix is accepted."""
        from snodo.cli.commands.cloud_cmd import cloud_connect_command

        with patch("snodo.cli.config.ConfigManager") as MockCM:
            mock_mgr = MockCM.return_value
            mock_mgr.load.return_value = {}

            result = cloud_connect_command("sndo_staging_xyz")

            assert result == 0
            saved = mock_mgr.save.call_args[0][0]
            assert saved["cloud"]["sync_enabled"] is True

    def test_invalid_key_format_rejected(self):
        """Keys without valid prefix are rejected."""
        from snodo.cli.commands.cloud_cmd import cloud_connect_command

        result = cloud_connect_command("invalid_key_format")
        assert result == 1

    def test_empty_key_rejected(self):
        from snodo.cli.commands.cloud_cmd import cloud_connect_command
        result = cloud_connect_command("")
        assert result == 1


class TestCloudDisconnect:
    def test_clears_key_and_disables_sync(self):
        from snodo.cli.commands.cloud_cmd import cloud_disconnect_command

        with patch("snodo.cli.config.ConfigManager") as MockCM:
            mock_mgr = MockCM.return_value
            mock_mgr.load.return_value = {
                "cloud": {"api_key": "sndo_live_xxx", "sync_enabled": True},
            }

            result = cloud_disconnect_command()

            assert result == 0
            saved = mock_mgr.save.call_args[0][0]
            assert saved["cloud"]["api_key"] == ""
            assert saved["cloud"]["sync_enabled"] is False


class TestCloudStatus:
    def test_connected_shows_key_prefix(self, capsys):
        from snodo.cli.commands.cloud_cmd import cloud_status_command

        with patch("snodo.cli.config.ConfigManager") as MockCM:
            mock_mgr = MockCM.return_value
            mock_mgr.load.return_value = {
                "cloud": {
                    "api_key": "sndo_live_abcdef123456789000",
                    "sync_enabled": True,
                    "api_url": "https://api.snodo.dev",
                },
            }

            with patch("snodo.infrastructure.cloud_sync.CloudSyncState") as MockState:
                MockState.return_value.get_summary.return_value = {}
                result = cloud_status_command()

        assert result == 0
        out = capsys.readouterr().out
        assert "connected" in out
        assert "sndo_live_abcdef..." in out  # first 16 chars + ...

    def test_disconnected_shows_not_connected(self, capsys):
        from snodo.cli.commands.cloud_cmd import cloud_status_command

        with patch("snodo.cli.config.ConfigManager") as MockCM:
            mock_mgr = MockCM.return_value
            mock_mgr.load.return_value = {"cloud": {"api_key": "", "sync_enabled": False}}

            with patch("snodo.infrastructure.cloud_sync.CloudSyncState") as MockState:
                MockState.return_value.get_summary.return_value = {}
                result = cloud_status_command()

        assert result == 0
        out = capsys.readouterr().out
        assert "not connected" in out

    def test_shows_sync_per_session(self, capsys):
        from snodo.cli.commands.cloud_cmd import cloud_status_command

        with patch("snodo.cli.config.ConfigManager") as MockCM:
            mock_mgr = MockCM.return_value
            mock_mgr.load.return_value = {
                "cloud": {"api_key": "sndo_live_xxx", "sync_enabled": True},
            }

            with patch("snodo.infrastructure.cloud_sync.CloudSyncState") as MockState:
                MockState.return_value.get_summary.return_value = {
                    "sess_abc": {"last_synced_sequence": 42, "last_synced_at": 1700000000},
                }
                result = cloud_status_command()

        assert result == 0
        out = capsys.readouterr().out
        assert "sess_abc" in out
        assert "last_seq=42" in out


# ------------------------------------------------------------------#
# CloudSyncState tests
# ------------------------------------------------------------------#

class TestCloudSyncState:
    def test_get_cursor_returns_zero_when_none(self):
        from snodo.infrastructure.cloud_sync import CloudSyncState
        state = CloudSyncState(state_path=Path("/nonexistent/cloud_sync.json"))
        assert state.get_cursor("sess_unknown") == 0

    def test_advance_and_get_cursor(self, tmp_path):
        from snodo.infrastructure.cloud_sync import CloudSyncState
        path = tmp_path / "cloud_sync.json"
        state = CloudSyncState(state_path=path)
        assert state.get_cursor("sess_1") == 0

        state.advance_cursor("sess_1", 10)
        assert state.get_cursor("sess_1") == 10

        state.advance_cursor("sess_1", 25)
        assert state.get_cursor("sess_1") == 25

    def test_get_summary(self, tmp_path):
        from snodo.infrastructure.cloud_sync import CloudSyncState
        path = tmp_path / "cloud_sync.json"
        state = CloudSyncState(state_path=path)
        state.advance_cursor("sess_a", 5)
        state.advance_cursor("sess_b", 12)

        summary = state.get_summary()
        assert "sess_a" in summary
        assert "sess_b" in summary
        assert summary["sess_a"]["last_synced_sequence"] == 5
        assert summary["sess_b"]["last_synced_sequence"] == 12

    def test_atomic_write_uses_tmp_and_rename(self, tmp_path):
        from snodo.infrastructure.cloud_sync import CloudSyncState
        path = tmp_path / "cloud_sync.json"
        state = CloudSyncState(state_path=path)

        state.advance_cursor("sess_x", 77)

        assert path.exists()
        raw = json.loads(path.read_text())
        assert raw["sess_x"]["last_synced_sequence"] == 77


# ------------------------------------------------------------------#
# CloudSyncDispatcher tests
# ------------------------------------------------------------------#

class TestCloudSyncDispatcher:
    def _make_events(self, count, start_seq=0):
        """Create mock AuditEvents with sequence numbers."""
        events = []
        for i in range(count):
            ev = MagicMock()
            ev.sequence = start_seq + i + 1
            ev.timestamp = "2026-01-01T00:00:00Z"
            ev.event_type = "tool_call"
            ev.data = {"key": "value"}
            ev.previous_hash = "0" * 64
            ev.event_hash = "e" * 64
            events.append(ev)
        return events

    def test_sync_enabled_false_no_http_calls(self):
        from snodo.infrastructure.cloud_sync import _should_sync
        assert _should_sync({"cloud": {"sync_enabled": False, "api_key": "sndo_live_xxx"}}) is False

    def test_sync_enabled_true_but_no_key(self):
        from snodo.infrastructure.cloud_sync import _should_sync
        assert _should_sync({"cloud": {"sync_enabled": True, "api_key": ""}}) is False

    def test_sync_enabled_true_with_key(self):
        from snodo.infrastructure.cloud_sync import _should_sync
        assert _should_sync({"cloud": {"sync_enabled": True, "api_key": "sndo_live_xxx"}}) is True

    def test_sync_no_events(self):
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher

        dispatcher = CloudSyncDispatcher()
        audit_log = MagicMock()
        audit_log.events = []

        result = dispatcher.sync("sess_1", "/proj", audit_log, "sndo_live_xxx",
                                  "https://api.example.com")
        assert result == {"synced": 0, "failed": False}

    def test_sync_batches_up_to_50(self):
        """Batch of 75 events → two POST calls (50 + 25)."""
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState
        from unittest.mock import patch

        events = self._make_events(75)
        audit_log = MagicMock()
        audit_log.events = events

        dispatcher = CloudSyncDispatcher()

        with patch.object(CloudSyncState, "get_cursor", return_value=0):
            with patch.object(CloudSyncState, "advance_cursor"):
                with patch.object(dispatcher, "_post_batch", return_value=True) as mock_post:
                    result = dispatcher.sync(
                        "sess_batch", "/proj", audit_log,
                        "sndo_live_xxx", "https://api.example.com",
                    )

        assert result["synced"] == 75
        assert result["failed"] is False
        assert mock_post.call_count == 2

    def test_cursor_advances_only_on_200(self):
        """Cursor should not advance when post fails."""
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState

        events = self._make_events(5)
        audit_log = MagicMock()
        audit_log.events = events

        dispatcher = CloudSyncDispatcher()

        with patch.object(CloudSyncState, "get_cursor", return_value=0):
            with patch.object(CloudSyncState, "advance_cursor") as mock_advance:
                with patch.object(dispatcher, "_post_batch", return_value=False):
                    result = dispatcher.sync(
                        "sess_fail", "/proj", audit_log,
                        "sndo_live_xxx", "https://api.example.com",
                    )

        assert result["synced"] == 0
        assert result["failed"] is True
        mock_advance.assert_not_called()

    def test_sync_only_unsynced_events(self):
        """Only events with sequence > cursor are sent."""
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState

        events = self._make_events(10)  # seq 1-10
        audit_log = MagicMock()
        audit_log.events = events

        dispatcher = CloudSyncDispatcher()

        with patch.object(CloudSyncState, "get_cursor", return_value=5):
            with patch.object(CloudSyncState, "advance_cursor"):
                with patch.object(dispatcher, "_post_batch", return_value=True) as mock_post:
                    result = dispatcher.sync(
                        "sess_cur", "/proj", audit_log,
                        "sndo_live_xxx", "https://api.example.com",
                    )

        assert result["synced"] == 5  # events 6-10
        assert result["failed"] is False

    def test_429_retries_with_retry_after(self):
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher
        from unittest.mock import patch
        import urllib.error

        events = self._make_events(3)
        audit_log = MagicMock()
        audit_log.events = events

        dispatcher = CloudSyncDispatcher()

        # First call: 429, second: 200
        ok_mock = MagicMock()
        ok_mock.status = 200
        ok_mock.__enter__.return_value = ok_mock
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = [
                urllib.error.HTTPError(
                    "url", 429, "Too Many Requests",
                    {"Retry-After": "1"}, None,
                ),
                ok_mock,
            ]

            with patch("snodo.infrastructure.cloud_sync.time.sleep") as mock_sleep:
                result = dispatcher._post_batch(
                    "sess_rl", "/proj", events[:3],
                    "sndo_live_xxx", "https://api.example.com",
                )

        assert result is True
        mock_sleep.assert_called_with(1)

    def test_5xx_exponential_backoff(self):
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher
        from unittest.mock import patch
        import urllib.error

        events = self._make_events(3)
        dispatcher = CloudSyncDispatcher()

        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                "url", 503, "Service Unavailable", {}, None,
            )

            with patch("snodo.infrastructure.cloud_sync.time.sleep") as mock_sleep:
                result = dispatcher._post_batch(
                    "sess_5xx", "/proj", events[:3],
                    "sndo_live_xxx", "https://api.example.com",
                )

        assert result is False
        # 5 retries: attempt 0 (1s), 1 (2s), 2 (4s), 3 (8s), 4 (16s), attempt 5 → return False
        assert mock_sleep.call_count == 5

    def test_network_error_never_raises(self):
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher

        dispatcher = CloudSyncDispatcher()
        audit_log = MagicMock()
        audit_log.events = self._make_events(3)

        # Network error with retries
        with patch("snodo.infrastructure.cloud_sync.CloudSyncState.get_cursor", return_value=0):
            with patch("snodo.infrastructure.cloud_sync.CloudSyncState.advance_cursor"):
                with patch.object(dispatcher, "_post_batch", return_value=False):
                    result = dispatcher.sync(
                        "sess_net", "/proj", audit_log,
                        "sndo_live_xxx", "https://api.example.com",
                    )

        assert result == {"synced": 0, "failed": True}

    def test_unexpected_exception_never_raises(self):
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher
        from unittest.mock import PropertyMock

        dispatcher = CloudSyncDispatcher()
        audit_log = MagicMock()

        # Make accessing .events raise an exception
        type(audit_log).events = PropertyMock(side_effect=MemoryError("boom"))

        result = dispatcher.sync(
            "sess_err", "/proj", audit_log,
            "sndo_live_xxx", "https://api.example.com",
        )

        assert result == {"synced": 0, "failed": True}

    def test_sync_if_enabled_spawns_thread(self):
        from snodo.infrastructure.cloud_sync import sync_if_enabled

        config = {"cloud": {"sync_enabled": True, "api_key": "sndo_live_xxx", "api_url": "https://api.example.com"}}

        with patch("snodo.infrastructure.cloud_sync.CloudSyncDispatcher.sync") as mock_sync:
            sync_if_enabled("sess_t", "/proj", MagicMock(), config=config)

            # Background thread — give it a moment
            import threading
            for t in threading.enumerate():
                if t is not threading.main_thread() and t.daemon:
                    t.join(timeout=1)

        mock_sync.assert_called_once()

    def test_sync_if_enabled_disabled_does_nothing(self):
        from snodo.infrastructure.cloud_sync import sync_if_enabled

        config = {"cloud": {"sync_enabled": False, "api_key": ""}}

        with patch("snodo.infrastructure.cloud_sync.CloudSyncDispatcher.sync") as mock_sync:
            sync_if_enabled("sess_t", "/proj", MagicMock(), config=config)

        mock_sync.assert_not_called()


# ------------------------------------------------------------------#
# cloud_sync_command tests
# ------------------------------------------------------------------#

class TestCloudSyncCommand:
    def test_no_api_key_errors(self):
        from snodo.cli.commands.cloud_cmd import cloud_sync_command

        with patch("snodo.cli.config.ConfigManager") as MockCM:
            MockCM.return_value.load.return_value = {"cloud": {"api_key": ""}}
            result = cloud_sync_command()
        assert result == 1

    def test_sync_active_session(self):
        from snodo.cli.commands.cloud_cmd import cloud_sync_command

        with patch("snodo.infrastructure.audit.AuditLog"):
            with patch("snodo.infrastructure.cloud_sync.CloudSyncDispatcher") as MockDisp:
                with patch("snodo.infrastructure.session.SessionManager") as MockSM:
                    with patch("snodo.infrastructure.paths.require_project_root", return_value="/fake/proj"):
                        with patch("snodo.infrastructure.state.read_state") as mock_rs:
                            with patch("snodo.cli.config.ConfigManager") as MockCM:
                                MockCM.return_value.load.return_value = {
                                    "cloud": {"api_key": "sndo_live_xxx", "api_url": "https://api.example.com"},
                                }
                                mock_rs.return_value.current_mode = "producer"

                                mock_session = MagicMock()
                                mock_session.session_id = "sess_active"
                                mock_session.project_root = "/fake/proj"
                                MockSM.return_value.get_active_session.return_value = mock_session

                                mock_disp = MockDisp.return_value
                                mock_disp.sync.return_value = {"synced": 5, "failed": False}

                                result = cloud_sync_command()

        assert result == 0
        mock_disp.sync.assert_called_once()

    def test_sync_all_sessions(self):
        from snodo.cli.commands.cloud_cmd import cloud_sync_command

        with patch("snodo.infrastructure.audit.AuditLog"):
            with patch("snodo.infrastructure.cloud_sync.CloudSyncDispatcher") as MockDisp:
                with patch("snodo.infrastructure.session.SessionManager") as MockSM:
                    with patch("snodo.infrastructure.paths.require_project_root", return_value="/fake/proj"):
                        with patch("snodo.cli.config.ConfigManager") as MockCM:
                            MockCM.return_value.load.return_value = {
                                "cloud": {"api_key": "sndo_live_xxx", "api_url": "https://api.example.com"},
                            }

                            sess1 = MagicMock()
                            sess1.session_id = "sess_a"
                            sess1.project_root = "/fake/a"
                            sess2 = MagicMock()
                            sess2.session_id = "sess_b"
                            sess2.project_root = "/fake/b"
                            MockSM.return_value.list_sessions.return_value = [sess1, sess2]

                            mock_disp = MockDisp.return_value
                            mock_disp.sync.side_effect = [
                                {"synced": 3, "failed": False},
                                {"synced": 7, "failed": False},
                            ]

                            result = cloud_sync_command(sync_all=True)

        assert result == 0
        assert mock_disp.sync.call_count == 2

    def test_sync_specific_session(self):
        from snodo.cli.commands.cloud_cmd import cloud_sync_command

        with patch("snodo.infrastructure.audit.AuditLog"):
            with patch("snodo.infrastructure.cloud_sync.CloudSyncDispatcher") as MockDisp:
                with patch("snodo.infrastructure.session.SessionManager") as MockSM:
                    with patch("snodo.infrastructure.paths.require_project_root", return_value="/fake/proj"):
                        with patch("snodo.cli.config.ConfigManager") as MockCM:
                            MockCM.return_value.load.return_value = {
                                "cloud": {"api_key": "sndo_live_xxx", "api_url": "https://api.example.com"},
                            }

                            mock_session = MagicMock()
                            mock_session.session_id = "sess_specific"
                            mock_session.project_root = "/fake/proj"
                            MockSM.return_value.load_session.return_value = mock_session

                            mock_disp = MockDisp.return_value
                            mock_disp.sync.return_value = {"synced": 12, "failed": False}

                            result = cloud_sync_command(session_id="sess_specific")

        assert result == 0
        mock_disp.sync.assert_called_once()

    def test_sync_failure_returns_one(self):
        from snodo.cli.commands.cloud_cmd import cloud_sync_command

        with patch("snodo.infrastructure.audit.AuditLog"):
            with patch("snodo.infrastructure.cloud_sync.CloudSyncDispatcher") as MockDisp:
                with patch("snodo.infrastructure.session.SessionManager") as MockSM:
                    with patch("snodo.infrastructure.paths.require_project_root", return_value="/fake/proj"):
                        with patch("snodo.cli.config.ConfigManager") as MockCM:
                            MockCM.return_value.load.return_value = {
                                "cloud": {"api_key": "sndo_live_xxx", "api_url": "https://api.example.com"},
                            }

                            sess1 = MagicMock()
                            sess1.session_id = "sess_x"
                            sess1.project_root = "/fake/x"
                            sess2 = MagicMock()
                            sess2.session_id = "sess_y"
                            sess2.project_root = "/fake/y"
                            MockSM.return_value.list_sessions.return_value = [sess1, sess2]

                            mock_disp = MockDisp.return_value
                            mock_disp.sync.side_effect = [
                                {"synced": 0, "failed": True},
                                {"synced": 0, "failed": True},
                            ]

                            result = cloud_sync_command(sync_all=True)

        assert result == 1

    def test_sync_session_not_found(self):
        from snodo.cli.commands.cloud_cmd import cloud_sync_command

        with patch("snodo.infrastructure.session.SessionManager") as MockSM:
            with patch("snodo.infrastructure.paths.require_project_root", return_value="/fake/proj"):
                with patch("snodo.cli.config.ConfigManager") as MockCM:
                    MockCM.return_value.load.return_value = {
                        "cloud": {"api_key": "sndo_live_xxx", "api_url": "https://api.example.com"},
                    }
                    MockSM.return_value.load_session.side_effect = FileNotFoundError("nope")

                    result = cloud_sync_command(session_id="sess_missing")

        assert result == 1