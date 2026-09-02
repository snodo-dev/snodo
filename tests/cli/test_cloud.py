"""Tests for cloud connect/disconnect/status and audit sync infrastructure.

FILE: tests/cli/test_cloud.py
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

# ------------------------------------------------------------------#
# Cloud connect / disconnect / status
# ------------------------------------------------------------------#

class TestCloudConnect:
    def test_valid_key_stored_and_sync_enabled(self, tmp_path):
        """snodo cloud connect stores key and enables sync."""
        from snodo.cli.commands.cloud_cmd import cloud_connect_command

        with patch("snodo.config.ConfigManager") as MockCM:
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

        with patch("snodo.config.ConfigManager") as MockCM:
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

        with patch("snodo.config.ConfigManager") as MockCM:
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

        with patch("snodo.config.ConfigManager") as MockCM:
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

        with patch("snodo.config.ConfigManager") as MockCM:
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

        with patch("snodo.config.ConfigManager") as MockCM:
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
        assert result["synced"] == 0
        assert result["failed"] is False

    def test_sync_batches_up_to_50(self):
        """Batch of 75 events → two POST calls (50 + 25)."""
        from unittest.mock import patch

        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState

        events = self._make_events(75)
        audit_log = MagicMock()
        audit_log.events = events

        dispatcher = CloudSyncDispatcher()

        with patch.object(CloudSyncState, "get_cursor", return_value=0):
            with patch.object(CloudSyncState, "advance_cursor"):
                with patch.object(dispatcher, "_post_batch",
                                  return_value=("delivered", "HTTP 200", 200)) as mock_post:
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
                with patch.object(dispatcher, "_post_batch",
                                  return_value=("retryable", "HTTP 500: boom", 500)):
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
                with patch.object(dispatcher, "_post_batch",
                                  return_value=("delivered", "HTTP 200", 200)):
                    result = dispatcher.sync(
                        "sess_cur", "/proj", audit_log,
                        "sndo_live_xxx", "https://api.example.com",
                    )

        assert result["synced"] == 5  # events 6-10
        assert result["failed"] is False

    def test_429_retries_with_retry_after(self):
        from unittest.mock import patch

        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher

        events = self._make_events(3)
        audit_log = MagicMock()
        audit_log.events = events

        dispatcher = CloudSyncDispatcher()

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.text = "ok"
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {"Retry-After": "1"}
        rate_limited.text = "rate limited"
        with patch("httpx.post", side_effect=[rate_limited, ok_resp]) as mock_post:
            with patch("snodo.infrastructure.cloud_sync.time.sleep") as mock_sleep:
                outcome, reason, status_code = dispatcher._post_batch(
                    "sess_rl", "/proj", events[:3],
                    "sndo_live_xxx", "https://api.example.com",
                )

        assert outcome == "delivered"
        mock_sleep.assert_called_with(1)
        assert mock_post.call_count == 2

    def test_5xx_exponential_backoff(self):
        from unittest.mock import patch

        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher

        events = self._make_events(3)
        dispatcher = CloudSyncDispatcher()

        svr_err = MagicMock()
        svr_err.status_code = 503
        svr_err.text = "service unavailable"
        with patch("httpx.post", return_value=svr_err) as mock_post:
            with patch("snodo.infrastructure.cloud_sync.time.sleep") as mock_sleep:
                outcome, reason, status_code = dispatcher._post_batch(
                    "sess_5xx", "/proj", events[:3],
                    "sndo_live_xxx", "https://api.example.com",
                )

        assert outcome == "retryable"
        # 5 retries: attempt 0 (1s), 1 (2s), 2 (4s), 3 (8s), 4 (16s), attempt 5 → return False
        assert mock_sleep.call_count == 5
        assert mock_post.call_count == 6  # _MAX_RETRIES + 1

    def test_post_batch_carries_project_id_and_display_name(self):
        """Transmitted cloud sync payload carries event.project_id and envelope display_name."""
        from unittest.mock import patch, MagicMock
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher

        dispatcher = CloudSyncDispatcher()
        events = self._make_events(2)
        for ev in events:
            ev.project_id = "github.com/snodo-dev/test-repo"

        captured_body = None

        def fake_post(url, content, headers, timeout):
            nonlocal captured_body
            captured_body = json.loads(content.decode())
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "ok"
            return resp

        with patch("httpx.post", side_effect=fake_post):
            outcome, reason, status = dispatcher._post_batch(
                "sess_pid_test",
                "/home/user/code/my-awesome-project",
                events,
                "sndo_live_xxx",
                "https://api.example.com",
            )

        assert outcome == "delivered"
        assert captured_body is not None
        assert captured_body["session_id"] == "sess_pid_test"
        assert captured_body["project_path"] == "/home/user/code/my-awesome-project"
        assert captured_body["display_name"] == "my-awesome-project"
        assert "project_name" not in captured_body
        assert len(captured_body["events"]) == 2
        for ev_payload in captured_body["events"]:
            assert ev_payload["project_id"] == "github.com/snodo-dev/test-repo"
            assert ev_payload["event_type"] == "tool_call"

    def test_network_error_never_raises(self):
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher

        dispatcher = CloudSyncDispatcher()
        audit_log = MagicMock()
        audit_log.events = self._make_events(3)

        # Network error with retries
        with patch("snodo.infrastructure.cloud_sync.CloudSyncState.get_cursor", return_value=0):
            with patch("snodo.infrastructure.cloud_sync.CloudSyncState.advance_cursor"):
                with patch.object(dispatcher, "_post_batch",
                                  return_value=("retryable", "Network error", None)):
                    result = dispatcher.sync(
                        "sess_net", "/proj", audit_log,
                        "sndo_live_xxx", "https://api.example.com",
                    )

        assert result["synced"] == 0
        assert result["failed"] is True
        assert result["pending"] == 3

    def test_unexpected_exception_never_raises(self):
        from unittest.mock import PropertyMock

        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher

        dispatcher = CloudSyncDispatcher()
        audit_log = MagicMock()

        # Make accessing .events raise an exception
        type(audit_log).events = PropertyMock(side_effect=MemoryError("boom"))

        result = dispatcher.sync(
            "sess_err", "/proj", audit_log,
            "sndo_live_xxx", "https://api.example.com",
        )

        assert result["synced"] == 0
        assert result["failed"] is True

    def test_sync_if_enabled_spawns_thread(self):
        import snodo.infrastructure.cloud_sync as cs
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
        # Drain the registered thread so it doesn't leak into later tests.
        cs._pending_syncs.clear()

    def test_sync_if_enabled_disabled_does_nothing(self):
        from snodo.infrastructure.cloud_sync import sync_if_enabled

        config = {"cloud": {"sync_enabled": False, "api_key": ""}}

        with patch("snodo.infrastructure.cloud_sync.CloudSyncDispatcher.sync") as mock_sync:
            sync_if_enabled("sess_t", "/proj", MagicMock(), config=config)

        mock_sync.assert_not_called()

    def test_refused_response_records_reason_range_and_skips_automatic_retry(self, tmp_path, monkeypatch):
        """A 400 refused response leaves cursor, records reason & range, and is skipped on automatic sync."""
        from unittest.mock import patch, MagicMock
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState

        state_path = tmp_path / "cloud_sync.json"
        monkeypatch.setattr("snodo.infrastructure.cloud_sync.resolve_home", lambda: tmp_path)

        events = self._make_events(5)
        audit_log = MagicMock()
        audit_log.events = events

        dispatcher = CloudSyncDispatcher()

        bad_resp = MagicMock()
        bad_resp.status_code = 400
        bad_resp.text = "Malformed event payload"

        with patch("httpx.post", return_value=bad_resp):
            res = dispatcher.sync("sess_refused", "/proj", audit_log, "sndo_live_xxx", "https://api.example.com")

        assert res["failed"] is True
        assert res["refused"] is True
        assert "Malformed event payload" in res["reason"]

        state = CloudSyncState(state_path)
        assert state.get_cursor("sess_refused") == 0
        assert state.is_refused("sess_refused") is True
        info = state.get_summary()["sess_refused"]
        assert info["refused_range"] == [1, 5]
        assert info["refused_status_code"] == 400

        with patch("httpx.post") as mock_post:
            res2 = dispatcher.sync("sess_refused", "/proj", audit_log, "sndo_live_xxx", "https://api.example.com", force=False)
            mock_post.assert_not_called()

        assert res2["synced"] == 0
        assert res2["refused"] is True

    def test_retryable_failure_is_retried_on_subsequent_sync(self, tmp_path, monkeypatch):
        """A 503 server error leaves cursor, does not set refused=True, and is retried on subsequent sync."""
        from unittest.mock import patch, MagicMock
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState

        state_path = tmp_path / "cloud_sync.json"
        monkeypatch.setattr("snodo.infrastructure.cloud_sync.resolve_home", lambda: tmp_path)

        events = self._make_events(5)
        audit_log = MagicMock()
        audit_log.events = events

        dispatcher = CloudSyncDispatcher()

        err_resp = MagicMock()
        err_resp.status_code = 503
        err_resp.text = "Service unavailable"

        with patch("httpx.post", return_value=err_resp):
            with patch("snodo.infrastructure.cloud_sync.time.sleep"):
                res = dispatcher.sync("sess_503", "/proj", audit_log, "sndo_live_xxx", "https://api.example.com")

        assert res["failed"] is True
        assert res.get("refused") is not True

        state = CloudSyncState(state_path)
        assert state.get_cursor("sess_503") == 0
        assert state.is_refused("sess_503") is False

        with patch("httpx.post", return_value=err_resp) as mock_post:
            with patch("snodo.infrastructure.cloud_sync.time.sleep"):
                dispatcher.sync("sess_503", "/proj", audit_log, "sndo_live_xxx", "https://api.example.com", force=False)
            assert mock_post.called

    def test_cloud_status_displays_blocked_refused_session(self, tmp_path, monkeypatch, capsys):
        """snodo cloud status displays BLOCKED (refused: <reason>, seq range) for refused sessions."""
        from snodo.infrastructure.cloud_sync import CloudSyncState
        from snodo.cli.commands.cloud_cmd import cloud_status_command

        state_path = tmp_path / "cloud_sync.json"
        monkeypatch.setattr("snodo.infrastructure.cloud_sync.resolve_home", lambda: tmp_path)

        state = CloudSyncState(state_path)
        state.record_refusal("sess_blocked", "HTTP 400: Malformed payload", first_seq=1, last_seq=10, status_code=400)

        with patch("snodo.config.ConfigManager") as MockCM:
            MockCM.return_value.load.return_value = {
                "cloud": {"api_key": "sndo_live_xxx", "api_url": "https://api.example.com"},
            }
            res = cloud_status_command()

        assert res == 0
        out = capsys.readouterr().out
        assert "BLOCKED (refused: HTTP 400: Malformed payload, seq 1-10)" in out

    def test_operator_explicit_retry_reattempts_refused_batch(self, tmp_path, monkeypatch):
        """Explicit retry with force=True re-attempts a refused batch."""
        from unittest.mock import patch, MagicMock
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState

        state_path = tmp_path / "cloud_sync.json"
        monkeypatch.setattr("snodo.infrastructure.cloud_sync.resolve_home", lambda: tmp_path)

        state = CloudSyncState(state_path)
        state.record_refusal("sess_blocked", "HTTP 400: Bad payload", first_seq=1, last_seq=5, status_code=400)

        events = self._make_events(5)
        audit_log = MagicMock()
        audit_log.events = events

        dispatcher = CloudSyncDispatcher()

        err_resp = MagicMock()
        err_resp.status_code = 400
        err_resp.text = "Still bad"

        with patch("httpx.post", return_value=err_resp) as mock_post:
            res = dispatcher.sync("sess_blocked", "/proj", audit_log, "sndo_live_xxx", "https://api.example.com", force=True)

        assert mock_post.called
        assert res["refused"] is True

    def test_success_after_refusal_clears_blocked_state(self, tmp_path, monkeypatch, capsys):
        """A 200 OK after refusal advances cursor and clears blocked state."""
        from unittest.mock import patch, MagicMock
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState
        from snodo.cli.commands.cloud_cmd import cloud_status_command

        state_path = tmp_path / "cloud_sync.json"
        monkeypatch.setattr("snodo.infrastructure.cloud_sync.resolve_home", lambda: tmp_path)

        state = CloudSyncState(state_path)
        state.record_refusal("sess_blocked", "HTTP 400: Bad payload", first_seq=1, last_seq=5, status_code=400)
        assert state.is_refused("sess_blocked") is True

        events = self._make_events(5)
        audit_log = MagicMock()
        audit_log.events = events

        dispatcher = CloudSyncDispatcher()

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.text = "ok"

        with patch("httpx.post", return_value=ok_resp):
            res = dispatcher.sync("sess_blocked", "/proj", audit_log, "sndo_live_xxx", "https://api.example.com", force=True)

        assert res["synced"] == 5
        assert res.get("refused") is not True
        assert state.get_cursor("sess_blocked") == 5
        assert state.is_refused("sess_blocked") is False

        with patch("snodo.config.ConfigManager") as MockCM:
            MockCM.return_value.load.return_value = {
                "cloud": {"api_key": "sndo_live_xxx", "api_url": "https://api.example.com"},
            }
            cloud_status_command()

        out = capsys.readouterr().out
        assert "BLOCKED" not in out
        assert "last_seq=5" in out


# ------------------------------------------------------------------#
# Bounded-wait sync behaviours (Fixes #142)
# ------------------------------------------------------------------#

class TestSyncIfEnabledBoundedWait:
    """A sync that completes within the budget is delivered and the cursor
    advances; one that exceeds the budget does not hang the command, leaves
    the cursor where it was, and produces the stderr line; a failing sync
    produces the stderr line without --verbose.

    The bounded wait happens at process exit (``flush_pending_syncs``, an
    atexit flush of the background threads started by ``sync_if_enabled``),
    once per process, not once per task.
    """

    def _config(self):
        return {"cloud": {"sync_enabled": True, "api_key": "sndo_live_xxx", "api_url": "https://api.example.com"}}

    def _events(self, count, start_seq=1):
        """Create mock events with real sequence numbers (1-based)."""
        evs = []
        for i in range(count):
            ev = MagicMock()
            ev.sequence = start_seq + i
            evs.append(ev)
        return evs

    def test_sync_completing_within_budget_is_delivered(self, tmp_path, capsys):
        """A sync that completes within the budget is delivered and the cursor advances."""
        import snodo.infrastructure.cloud_sync as cs
        from snodo.infrastructure.cloud_sync import CloudSyncState, flush_pending_syncs

        state_path = tmp_path / "cloud_sync.json"
        state = CloudSyncState(state_path=state_path)
        state.advance_cursor("sess_ok", 0)

        audit_log = MagicMock()
        audit_log.events = []

        before = len(cs._pending_syncs)
        with patch("snodo.infrastructure.cloud_sync.CloudSyncState", return_value=state):
            with patch.object(cs.CloudSyncDispatcher, "sync",
                              return_value={"synced": 5, "failed": False, "pending": 0}):
                cs.sync_if_enabled("sess_ok", "/proj", audit_log, config=self._config())
                assert len(cs._pending_syncs) == before + 1
                flush_pending_syncs()

        # The thread finished; the pending entry was drained; no stderr line.
        assert len(cs._pending_syncs) == before
        assert capsys.readouterr().err == ""

    def test_sync_exceeding_budget_does_not_hang_and_reports(self, tmp_path, capsys):
        """A sync that exceeds the budget does not hang the command, leaves the
        cursor where it was, and produces the stderr line. The reported pending
        count is the unsynced backlog, not the size of the whole log."""
        import time

        import snodo.infrastructure.cloud_sync as cs
        from snodo.infrastructure.cloud_sync import CloudSyncState, flush_pending_syncs

        state_path = tmp_path / "cloud_sync.json"
        state = CloudSyncState(state_path=state_path)
        # Cursor at 5: events 1-5 are synced, so the unsynced backlog is 3 even
        # though the log holds 8 events. The report must say 3, not 8.
        state.advance_cursor("sess_slow", 5)

        audit_log = MagicMock()
        audit_log.events = self._events(8)  # sequences 1..8

        def slow_sync(*args, **kwargs):
            time.sleep(10)  # far beyond the budget
            return {"synced": 0, "failed": False, "pending": 3}

        start = time.monotonic()
        with patch("snodo.infrastructure.cloud_sync.CloudSyncState", return_value=state):
            with patch.object(cs.CloudSyncDispatcher, "sync", side_effect=slow_sync):
                cs.sync_if_enabled("sess_slow", "/proj", audit_log, config=self._config())
                flush_pending_syncs()
        elapsed = time.monotonic() - start

        # The flush did not hang: it returned within the bounded budget.
        assert elapsed < 8, f"flush_pending_syncs blocked for {elapsed:.1f}s"

        # The cursor was not advanced (the sync was abandoned).
        assert state.get_cursor("sess_slow") == 5

        err = capsys.readouterr().err
        assert "cloud sync still in progress" in err
        assert "3 event(s) pending" in err
        assert "8 event(s) pending" not in err

    def test_multiple_pending_syncs_stay_within_one_budget(self, tmp_path, capsys):
        """Several pending syncs against a cloud that never responds keep the
        total flush time within one budget, not one budget per sync."""
        import time

        import snodo.infrastructure.cloud_sync as cs
        from snodo.infrastructure.cloud_sync import CloudSyncState, flush_pending_syncs

        state_path = tmp_path / "cloud_sync.json"
        state = CloudSyncState(state_path=state_path)
        state.advance_cursor("sess_1", 0)
        state.advance_cursor("sess_2", 0)
        state.advance_cursor("sess_3", 0)

        def slow_sync(*args, **kwargs):
            time.sleep(10)  # far beyond the budget
            return {"synced": 0, "failed": False, "pending": 1}

        start = time.monotonic()
        with patch("snodo.infrastructure.cloud_sync.CloudSyncState", return_value=state):
            with patch.object(cs.CloudSyncDispatcher, "sync", side_effect=slow_sync):
                for sid in ("sess_1", "sess_2", "sess_3"):
                    audit_log = MagicMock()
                    audit_log.events = self._events(1)
                    cs.sync_if_enabled(sid, "/proj", audit_log, config=self._config())
                flush_pending_syncs()
        elapsed = time.monotonic() - start

        # Three syncs, one budget: the flush must not scale with the count.
        assert elapsed < 8, f"flush_pending_syncs blocked for {elapsed:.1f}s across 3 syncs"

        err = capsys.readouterr().err
        assert err.count("cloud sync still in progress") == 3

    def test_failing_sync_reports_on_stderr_without_verbose(self, tmp_path, capsys):
        """A failing sync produces the stderr line without --verbose. The
        reported pending count is the unsynced backlog, not the log size."""
        import snodo.infrastructure.cloud_sync as cs
        from snodo.infrastructure.cloud_sync import CloudSyncState, flush_pending_syncs

        state_path = tmp_path / "cloud_sync.json"
        state = CloudSyncState(state_path=state_path)
        # Cursor at 6: events 1-6 synced, so the unsynced backlog is 2 even
        # though the log holds 8 events.
        state.advance_cursor("sess_fail", 6)

        audit_log = MagicMock()
        audit_log.events = self._events(8)  # sequences 1..8

        with patch("snodo.infrastructure.cloud_sync.CloudSyncState", return_value=state):
            with patch.object(cs.CloudSyncDispatcher, "sync",
                              return_value={"synced": 0, "failed": True, "pending": 2}):
                cs.sync_if_enabled("sess_fail", "/proj", audit_log, config=self._config())
                flush_pending_syncs()

        err = capsys.readouterr().err
        assert "cloud sync failed" in err
        assert "2 event(s) pending" in err
        assert "8 event(s) pending" not in err

    def test_exit_code_unaffected_by_sync_outcome(self, tmp_path, capsys):
        """The CLI's exit code is unaffected by sync outcome — sync_if_enabled
        returns None and never raises."""
        import snodo.infrastructure.cloud_sync as cs
        from snodo.infrastructure.cloud_sync import sync_if_enabled

        audit_log = MagicMock()
        audit_log.events = self._events(2)

        with patch.object(cs.CloudSyncDispatcher, "sync",
                          return_value={"synced": 0, "failed": True, "pending": 2}):
            result = sync_if_enabled("sess_x", "/proj", audit_log, config=self._config())

        assert result is None
        # Drain the registered thread so it doesn't leak into later tests.
        cs.flush_pending_syncs()
        capsys.readouterr()  # drain stderr


# ------------------------------------------------------------------#
# cloud status pending/error reporting (Fixes #142)
# ------------------------------------------------------------------#

class TestCloudStatusPending:
    def test_status_reports_pending_count_and_last_error(self, tmp_path, capsys):
        """cloud status reports a non-zero pending count for a session with
        unsynced events, and the last error after a failure."""
        from snodo.cli.commands.cloud_cmd import cloud_status_command

        with patch("snodo.config.ConfigManager") as MockCM:
            mock_mgr = MockCM.return_value
            mock_mgr.load.return_value = {
                "cloud": {"api_key": "sndo_live_xxx", "sync_enabled": True},
            }

            with patch("snodo.infrastructure.cloud_sync.CloudSyncState") as MockState:
                MockState.return_value.get_summary.return_value = {
                    "sess_pending": {
                        "last_synced_sequence": 10,
                        "last_synced_at": 1700000000,
                        "pending_count": 7,
                        "last_attempt_at": 1700000100,
                        "last_error": "HTTP 500: internal error",
                    },
                }
                result = cloud_status_command()

        assert result == 0
        out = capsys.readouterr().out
        assert "sess_pending" in out
        assert "pending=7" in out
        assert "last_error: HTTP 500: internal error" in out

    def test_status_clears_error_after_success(self, tmp_path, capsys):
        """After a confirmed success the pending count is zero and the last
        error is gone."""
        from snodo.infrastructure.cloud_sync import CloudSyncState

        path = tmp_path / "cloud_sync.json"
        state = CloudSyncState(state_path=path)
        state.record_attempt("sess_ok", pending=5, error="HTTP 500: boom")
        state.advance_cursor("sess_ok", 5)

        summary = state.get_summary()["sess_ok"]
        assert summary["pending_count"] == 0
        assert "last_error" not in summary


# ------------------------------------------------------------------#
# cloud_sync_command tests
# ------------------------------------------------------------------#

class TestCloudSyncCommand:
    def test_no_api_key_errors(self):
        from snodo.cli.commands.cloud_cmd import cloud_sync_command

        with patch("snodo.config.ConfigManager") as MockCM:
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
                            with patch("snodo.config.ConfigManager") as MockCM:
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
                        with patch("snodo.config.ConfigManager") as MockCM:
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
                        with patch("snodo.config.ConfigManager") as MockCM:
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
                        with patch("snodo.config.ConfigManager") as MockCM:
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
                with patch("snodo.config.ConfigManager") as MockCM:
                    MockCM.return_value.load.return_value = {
                        "cloud": {"api_key": "sndo_live_xxx", "api_url": "https://api.example.com"},
                    }
                    MockSM.return_value.load_session.side_effect = FileNotFoundError("nope")

                    result = cloud_sync_command(session_id="sess_missing")

        assert result == 1

    def test_sync_corrupt_audit_log_reports_failure(self):
        from snodo.core.interfaces import AuditError

        from snodo.cli.commands.cloud_cmd import cloud_sync_command

        with patch("snodo.infrastructure.audit.AuditLog", side_effect=AuditError("corrupt chain")):
            with patch("snodo.infrastructure.cloud_sync.CloudSyncDispatcher"):
                with patch("snodo.infrastructure.session.SessionManager") as MockSM:
                    with patch("snodo.infrastructure.paths.require_project_root", return_value="/fake/proj"):
                        with patch("snodo.config.ConfigManager") as MockCM:
                            MockCM.return_value.load.return_value = {
                                "cloud": {"api_key": "sndo_live_xxx", "api_url": "https://api.example.com"},
                            }
                            sess = MagicMock()
                            sess.session_id = "sess_corrupt"
                            sess.project_root = "/fake/proj"
                            MockSM.return_value.load_session.return_value = sess

                            result = cloud_sync_command(session_id="sess_corrupt")

        assert result == 1


