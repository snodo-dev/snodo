"""Tests for cloud connect/disconnect/status and audit sync infrastructure.

FILE: tests/cli/test_cloud.py
"""

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ------------------------------------------------------------------#
# Cloud connect / disconnect / status
# ------------------------------------------------------------------#

class TestCloudConnect:
    def test_valid_key_stored_and_sync_enabled(self, tmp_path, monkeypatch):
        """snodo cloud connect stores key and enables sync."""
        monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
        from snodo.cli.commands.cloud_cmd import cloud_connect_command

        with patch("snodo.config.ConfigManager") as MockCM:
            mock_mgr = MockCM.return_value
            result = cloud_connect_command("sndo_live_abcdef123456789")

            assert result == 0
            assert [call.args for call in mock_mgr.set_value.call_args_list] == [
                (("cloud", "api_key"), "sndo_live_abcdef123456789"),
                (("cloud", "sync_enabled"), True),
            ]

    def test_valid_staging_key(self, tmp_path, monkeypatch):
        """Staging key prefix is accepted."""
        monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
        from snodo.cli.commands.cloud_cmd import cloud_connect_command

        with patch("snodo.config.ConfigManager") as MockCM:
            mock_mgr = MockCM.return_value
            result = cloud_connect_command("sndo_staging_xyz")

            assert result == 0
            assert [call.args for call in mock_mgr.set_value.call_args_list] == [
                (("cloud", "api_key"), "sndo_staging_xyz"),
                (("cloud", "sync_enabled"), True),
            ]

    def test_invalid_key_format_rejected(self):
        """Keys without valid prefix are rejected."""
        from snodo.cli.commands.cloud_cmd import cloud_connect_command

        result = cloud_connect_command("invalid_key_format")
        assert result == 1

    def test_empty_key_rejected(self):
        from snodo.cli.commands.cloud_cmd import cloud_connect_command
        result = cloud_connect_command("")
        assert result == 1

    @pytest.mark.parametrize("revocation_fails", [False, True])
    def test_connect_replaces_oauth_and_attempts_best_effort_revocation(
        self, monkeypatch, tmp_path, capsys, revocation_fails,
    ):
        monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
        from snodo.infrastructure.cloud_oauth_store import (
            CloudOAuthState, load_oauth_state, save_oauth_state,
        )
        save_oauth_state(CloudOAuthState("client-id", "access-secret", "refresh-secret"))
        attempts = []

        class Client:
            def __init__(self, _config):
                pass

            def discover(self):
                return type("Metadata", (), {"revocation_endpoint": "https://cloud.example/revoke"})()

            def revoke(self, client_id, token):
                attempts.append((client_id, token))
                if revocation_fails:
                    raise RuntimeError("revocation failed")

        monkeypatch.setattr("snodo.infrastructure.cloud_oauth_client.CloudOAuthClient", Client)
        with patch("snodo.config.ConfigManager") as manager:
            manager.return_value.load.return_value = {}
            from snodo.cli.commands.cloud_cmd import cloud_connect_command
            assert cloud_connect_command("sndo_live_valid") == 0
            assert manager.return_value.set_value.call_args_list[0].args == (
                ("cloud", "api_key"), "sndo_live_valid",
            )
        assert load_oauth_state().access_token is None
        assert attempts == [("client-id", "refresh-secret")]
        output = capsys.readouterr().out
        assert "API key authentication is now in use" in output
        assert "access-secret" not in output and "refresh-secret" not in output
        if revocation_fails:
            assert "Cloud token revocation failed; local sign-out is complete." in output

        with patch("snodo.config.ConfigManager") as manager, patch(
            "snodo.infrastructure.cloud_sync.CloudSyncState",
        ) as sync_state:
            manager.return_value.load.return_value = {
                "cloud": {"api_key": "sndo_live_valid", "sync_enabled": True},
            }
            sync_state.return_value.get_summary.return_value = {}
            from snodo.cli.commands.cloud_cmd import cloud_status_command
            assert cloud_status_command() == 0
        assert "Authentication: API key" in capsys.readouterr().out

    def test_connect_without_oauth_keeps_existing_behavior(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
        from snodo.infrastructure.cloud_oauth_store import load_oauth_state
        with patch("snodo.config.ConfigManager"):
            from snodo.cli.commands.cloud_cmd import cloud_connect_command
            assert cloud_connect_command("sndo_live_valid") == 0
        assert load_oauth_state().access_token is None
        assert "API key authentication is now in use" not in capsys.readouterr().out


class TestCloudDisconnect:
    def test_clears_key_and_disables_sync(self):
        from snodo.cli.commands.cloud_cmd import cloud_disconnect_command

        with patch("snodo.config.ConfigManager") as MockCM:
            mock_mgr = MockCM.return_value
            result = cloud_disconnect_command()

            assert result == 0
            assert [call.args for call in mock_mgr.set_value.call_args_list] == [
                (("cloud", "api_key"), ""),
                (("cloud", "sync_enabled"), False),
            ]

    def test_clears_oauth_state_too(self, monkeypatch):
        from snodo.cli.commands.cloud_cmd import cloud_disconnect_command
        cleared = []
        monkeypatch.setattr("snodo.infrastructure.cloud_oauth_store.clear_oauth_state", lambda: cleared.append(True))
        with patch("snodo.config.ConfigManager"):
            assert cloud_disconnect_command() == 0
        assert cleared == [True]


class TestCloudLogout:
    @pytest.mark.parametrize(("refresh", "failure"), [("refresh-secret", False), (None, False), ("refresh-secret", True)])
    def test_clears_tokens_and_revokes_best_effort(self, monkeypatch, tmp_path, capsys, refresh, failure):
        monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
        from snodo.infrastructure.cloud_oauth_store import CloudOAuthState, load_oauth_state, save_oauth_state
        save_oauth_state(CloudOAuthState("client", "access-secret", refresh, 1234))
        calls = []
        class Client:
            def __init__(self, _config): pass
            def discover(self):
                return type("Metadata", (), {"revocation_endpoint": "https://cloud.example/revoke"})()
            def revoke(self, client_id, token):
                calls.append((client_id, token))
                if failure:
                    raise RuntimeError("failure")
        monkeypatch.setattr("snodo.infrastructure.cloud_oauth_client.CloudOAuthClient", Client)
        with patch("snodo.config.ConfigManager") as manager:
            manager.return_value.load.return_value = {}
            from snodo.cli.commands.cloud_cmd import cloud_logout_command
            assert cloud_logout_command() == 0
        assert load_oauth_state().access_token is None
        assert (len(calls) == 1) is bool(refresh)
        output = capsys.readouterr().out
        assert "access-secret" not in output and "refresh-secret" not in output
        if refresh and failure:
            assert "local sign-out is complete" in output


class TestCloudOAuthLogin:
    def _setup(self, monkeypatch, tmp_path, *, key="", result=("auth-code", "http://localhost:4312/callback")):
        import sys
        import types
        from snodo.infrastructure.cloud_oauth_store import CloudOAuthState

        monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
        calls = {"registered": 0, "saved": None, "opened": [], "exchange": []}
        state = CloudOAuthState(
            client_id="existing-client" if (tmp_path / "reuse").exists() else None,
            registered_client_name="snodo CLI (test-host)" if (tmp_path / "reuse").exists() else None,
        )
        class Client:
            client_name = "snodo CLI (test-host)"
            def __init__(self, _config): pass
            def discover(self): return object()
            def register_client(self):
                calls["registered"] += 1
                return "new-client"
            def authorization_url(self, client_id, redirect_uri, _verifier, _state):
                calls["opened"].append((client_id, redirect_uri))
                return "https://cloud.example/authorize?secret=never-print"
            def exchange_code(self, client_id, code, redirect_uri, verifier):
                calls["exchange"].append((client_id, code, redirect_uri, verifier))
                return {"access_token": "not-a-jwt-secret", "refresh_token": "refresh-secret", "expires_in": 3600}
        def receive(url, _state, **kwargs):
            url = url("http://localhost:4312/callback") if callable(url) else url
            if kwargs.get("open_browser") is not None:
                assert kwargs["open_browser"](url) is False
                print(url)
            return result
        client_module = types.ModuleType("stub_client")
        loop_module = types.ModuleType("stub_loop")
        store_module = types.ModuleType("stub_store")
        client_module.CloudOAuthClient = Client
        client_module.CloudOAuthError = RuntimeError
        client_module.oauth_state = lambda: "state-value"
        client_module.pkce_verifier = lambda: "pkce-secret"
        loop_module.CloudOAuthLoopbackError = type("CloudOAuthLoopbackError", (RuntimeError,), {})
        loop_module.receive_authorization_code = receive
        store_module.CloudOAuthState = CloudOAuthState
        store_module.load_oauth_state = lambda: state
        store_module.save_oauth_state = lambda value: calls.update(saved=value)
        monkeypatch.setitem(sys.modules, "snodo.infrastructure.cloud_oauth_client", client_module)
        monkeypatch.setitem(sys.modules, "snodo.infrastructure.cloud_oauth_loopback", loop_module)
        monkeypatch.setitem(sys.modules, "snodo.infrastructure.cloud_oauth_store", store_module)
        from unittest.mock import MagicMock
        config = {"cloud": {"api_key": key}}
        mgr = MagicMock()
        mgr.load.return_value = config
        monkeypatch.setattr("snodo.config.ConfigManager", lambda: mgr)
        return calls, mgr

    def test_happy_path_enables_sync_without_writing_api_key(self, monkeypatch, tmp_path, capsys):
        calls, mgr = self._setup(monkeypatch, tmp_path, key="")
        from snodo.cli.commands.cloud_cmd import cloud_login_command
        assert cloud_login_command() == 0
        assert mgr.set_value.call_count == 1
        assert mgr.set_value.call_args.args == (("cloud", "sync_enabled"), True)
        assert calls["registered"] == 1
        assert calls["saved"].registered_client_name == "snodo CLI (test-host)"
        assert calls["saved"].access_token == "not-a-jwt-secret"
        output = capsys.readouterr().out
        assert "not-a-jwt-secret" not in output and "refresh-secret" not in output

    def test_reuses_client_id(self, monkeypatch, tmp_path):
        (tmp_path / "reuse").touch()
        calls, _mgr = self._setup(monkeypatch, tmp_path)
        from snodo.cli.commands.cloud_cmd import cloud_login_command
        assert cloud_login_command() == 0
        assert calls["registered"] == 0
        assert calls["exchange"][0][0] == "existing-client"

    def test_reregisters_client_when_cached_name_is_stale(self, monkeypatch, tmp_path):
        (tmp_path / "reuse").touch()
        calls, _mgr = self._setup(monkeypatch, tmp_path)
        import sys
        state_module = sys.modules["snodo.infrastructure.cloud_oauth_store"]
        state = state_module.load_oauth_state()
        state_module.load_oauth_state = lambda: type(state)(client_id="existing-client", registered_client_name="Snodo")
        from snodo.cli.commands.cloud_cmd import cloud_login_command
        assert cloud_login_command() == 0
        assert calls["registered"] == 1
        assert calls["exchange"][0][0] == "new-client"

    def test_no_browser_prints_authorization_url(self, monkeypatch, tmp_path, capsys):
        self._setup(monkeypatch, tmp_path)
        from snodo.cli.commands.cloud_cmd import cloud_login_command
        assert cloud_login_command(no_browser=True) == 0
        assert "https://cloud.example/authorize" in capsys.readouterr().out

    @pytest.mark.parametrize(("message", "expected"), [
        ("Timed out waiting for the OAuth callback.", "timed out"),
        ("The authorization server returned an error.", "denied"),
    ])
    def test_callback_failures_are_actionable(self, monkeypatch, tmp_path, capsys, message, expected):
        self._setup(monkeypatch, tmp_path)
        import sys
        loopback = sys.modules["snodo.infrastructure.cloud_oauth_loopback"]
        loopback_error = loopback.CloudOAuthLoopbackError
        loopback.receive_authorization_code = lambda *_args, **_kwargs: (_ for _ in ()).throw(loopback_error(message))
        from snodo.cli.commands.cloud_cmd import cloud_login_command
        assert cloud_login_command() == 1
        output = capsys.readouterr().err.lower()
        assert expected in output


class TestCloudStatus:
    @pytest.mark.parametrize(
        ("expires_at", "expected"),
        [
            (2_000, "1970-01-01 00:33:20 UTC (in 10m)"),
            (1_000, "1970-01-01 00:16:40 UTC (expired; next sync will refresh it)"),
        ],
    )
    @pytest.mark.parametrize(
        ("claims", "token"),
        [
            ({"org_id": "org-123", "sub": "private-user", "provider": "private-provider"}, None),
            ({"sub": "private-user", "provider": "private-provider"}, None),
            (None, "not-a-jwt-private-token"),
        ],
    )
    def test_oauth_status_expiry_org_and_token_redaction(
        self, monkeypatch, tmp_path, capsys, expires_at, expected, claims, token,
    ):
        monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
        monkeypatch.setattr("snodo.cli.commands.cloud_cmd.time.time", lambda: 1_400)
        if claims is not None:
            encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
            token = f"header.{encoded}.signature"
        from snodo.infrastructure.cloud_oauth_store import CloudOAuthState, save_oauth_state
        save_oauth_state(CloudOAuthState("client", token, "private-refresh-token", expires_at))
        from snodo.cli.commands.cloud_cmd import cloud_status_command
        with patch("snodo.config.ConfigManager") as manager, patch("snodo.infrastructure.cloud_sync.CloudSyncState") as state:
            manager.return_value.load.return_value = {"cloud": {"sync_enabled": True}}
            state.return_value.get_summary.return_value = {}
            assert cloud_status_command() == 0
        output = capsys.readouterr().out
        assert expected in output
        assert f"Account/organisation: {'org-123' if claims and 'org_id' in claims else 'unknown'}" in output
        assert token not in output
        assert "private-user" not in output and "private-provider" not in output
        assert "private-refresh-token" not in output

    def test_oauth_status_and_dual_credential_precedence_are_redacted(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("SNODO_HOME", str(tmp_path / "home"))
        from snodo.infrastructure.cloud_oauth_store import CloudOAuthState, save_oauth_state
        save_oauth_state(CloudOAuthState("client", "access-secret", "refresh-secret", 1_900_000_000))
        from snodo.cli.commands.cloud_cmd import cloud_status_command
        with patch("snodo.config.ConfigManager") as manager, patch("snodo.infrastructure.cloud_sync.CloudSyncState") as state:
            manager.return_value.load.return_value = {"cloud": {"api_key": "api-key-secret", "sync_enabled": True}}
            state.return_value.get_summary.return_value = {}
            assert cloud_status_command() == 0
        out = capsys.readouterr().out
        assert "Authentication: OAuth login" in out
        assert "OAuth login takes priority" in out
        assert "Refresh token present: yes" in out
        assert "expires" in out
        assert all(secret not in out for secret in ("access-secret", "refresh-secret", "api-key-secret"))

    def test_connected_reports_key_method_without_key(self, capsys):
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
        assert "Authentication: API key" in out
        assert "sndo_live_abcdef123456789000" not in out

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

    def test_shows_sessionless_project_cursor(self, capsys):
        from snodo.cli.commands.cloud_cmd import cloud_status_command

        with patch("snodo.config.ConfigManager") as MockCM:
            MockCM.return_value.load.return_value = {
                "cloud": {"api_key": "sndo_live_xxx", "sync_enabled": True},
            }
            with patch("snodo.infrastructure.cloud_sync.CloudSyncState") as MockState:
                MockState.return_value.get_summary.return_value = {
                    "project:github.com/example/project": {
                        "last_synced_sequence": 12,
                    },
                }
                MockState.return_value.is_refused.return_value = False
                assert cloud_status_command() == 0

        output = capsys.readouterr().out
        assert "project (sessionless) [github.com/example/project]" in output
        assert "last_seq=12" in output


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

    def test_stale_route_and_global_refusals_do_not_block_session(self, tmp_path):
        from snodo.infrastructure.cloud_sync import CloudSyncState

        path = tmp_path / "cloud_sync.json"
        path.write_text(json.dumps({
            "sess_20260916_prod_0e250d": {
                "refused": True,
                "refused_status_code": 404,
                "pending_count": 2319,
            },
            "": {"refused": True, "refused_status_code": 404, "pending_count": 2319},
        }))
        state = CloudSyncState(path)

        assert state.is_refused("sess_20260916_prod_0e250d") is False
        assert state.is_refused("sess_other") is False

    def test_sync_retries_exact_legacy_404_and_global_state_shape(self, tmp_path, monkeypatch):
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState

        path = tmp_path / "cloud_sync.json"
        path.write_text(json.dumps({
            "sess_20260916_prod_0e250d": {
                "refused": True,
                "refused_reason": "HTTP 404 route mismatch",
                "refused_status_code": 404,
                "pending_count": 2319,
            },
            "": {
                "refused": True,
                "refused_reason": "HTTP 404 route mismatch",
                "refused_status_code": 404,
                "pending_count": 2319,
            },
        }))
        monkeypatch.setattr("snodo.infrastructure.cloud_sync.resolve_home", lambda: tmp_path)
        event = MagicMock(
            sequence=1,
            timestamp="2026-01-01T00:00:00Z",
            event_type="transition",
            project_id="",
            data={"from_mode": "idle", "to_mode": "running", "task_ref": "task-1"},
            previous_hash="0" * 64,
            event_hash="e" * 64,
        )
        audit_log = MagicMock(events=[event])
        dispatcher = CloudSyncDispatcher()
        with patch.object(dispatcher, "_post_batch", return_value=("delivered", "HTTP 200", 200)) as post:
            result = dispatcher.sync(
                "sess_20260916_prod_0e250d", "/proj", audit_log,
                "sndo_live_xxx", "https://api.example.com",
            )

        post.assert_called_once()
        assert result["synced"] == 1
        assert result["failed"] is False
        assert CloudSyncState(path).is_refused("sess_20260916_prod_0e250d") is False

    def test_legacy_413_batch_is_recheckable_but_single_oversize_is_terminal(self, tmp_path):
        from snodo.infrastructure.cloud_sync import CloudSyncState

        state = CloudSyncState(tmp_path / "cloud_sync.json")
        state.record_refusal("sess_batch", "HTTP 413", 1, 50, 413)
        assert state.is_refused("sess_batch") is False
        state.record_refusal(
            "sess_single", "single event sequence 1 is 6000000 bytes", 1, 1, 413,
        )
        assert state.is_refused("sess_single") is True


# ------------------------------------------------------------------#
# CloudSyncDispatcher tests
# ------------------------------------------------------------------#

class TestCloudSyncDispatcher:
    @pytest.fixture(autouse=True)
    def _provide_test_lease(self):
        import time
        from unittest.mock import patch
        from snodo.infrastructure import cloud_lease

        cloud_lease.reset_admission_state()
        test_lease = cloud_lease.CloudLease(
            jti="ls_test_lease",
            token="tok_test_lease",
            expires_at=time.time() + 3600,
        )
        with (
            patch.object(cloud_lease, "get_current_lease", return_value=test_lease),
            patch.object(cloud_lease, "get_admission_lease", return_value=test_lease),
        ):
            yield test_lease
        cloud_lease.reset_admission_state()

    def _make_events(self, count, start_seq=0):
        """Create mock AuditEvents with sequence numbers."""
        events = []
        for i in range(count):
            ev = MagicMock()
            ev.sequence = start_seq + i + 1
            ev.timestamp = "2026-01-01T00:00:00Z"
            ev.event_type = "transition"
            ev.data = {
                "from_mode": "idle",
                "to_mode": "running",
                "task_ref": "task-1",
                "key": "value",
            }
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

    def test_validation_failure_names_event_and_sequence_without_tag_union(self):
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher

        event = self._make_events(1)[0]
        event.sequence = 2734
        event.timestamp = 123
        dispatcher = CloudSyncDispatcher()

        outcome, reason, status = dispatcher._post_batch(
            "sess_invalid", "/proj", [event], "sndo_live_xxx",
            "https://api.example.com",
        )

        assert outcome == "retryable"
        assert status is None
        assert "transition" in reason
        assert "2734" in reason
        assert "Client-side cloud sync validation failed" in reason
        assert "retry with `snodo cloud sync --all --force`" in reason
        assert "project_announced" not in reason

    def test_undeclared_event_mid_chain_and_later_events_are_sent(self):
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState

        events = self._make_events(3)
        events[1].event_type = "legacy_event_from_old_release"
        events[1].data = {"old_field": "preserved"}
        audit_log = MagicMock(events=events)
        dispatcher = CloudSyncDispatcher()
        sent = []

        def accepted(_url, content, **_kwargs):
            sent.extend(event["sequence"] for event in json.loads(content)["events"])
            response = MagicMock(status_code=200, text="ok")
            return response

        with (
            patch.object(CloudSyncState, "get_cursor", return_value=0),
            patch.object(CloudSyncState, "advance_cursor"),
            patch.object(CloudSyncState, "clear_refusal"),
            patch.object(CloudSyncState, "record_attempt"),
            patch("httpx.post", side_effect=accepted),
        ):
            result = dispatcher.sync(
                "sess_legacy", "/proj", audit_log,
                "sndo_live_xxx", "https://api.example.com",
            )

        assert result["synced"] == 3
        assert result["failed"] is False
        assert sent == [1, 2, 3]

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

    def test_large_events_are_partitioned_below_payload_limit(self):
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState

        events = self._make_events(12)
        for event in events:
            event.data["task_ref"] = "x" * 500_000
        audit_log = MagicMock()
        audit_log.events = events
        dispatcher = CloudSyncDispatcher()
        payload_sizes = []

        def accepted(_url, **kwargs):
            payload_sizes.append(len(kwargs["content"]))
            response = MagicMock(status_code=200, text="ok")
            return response

        with patch.object(CloudSyncState, "get_cursor", return_value=0):
            with patch.object(CloudSyncState, "advance_cursor"):
                with patch("httpx.post", side_effect=accepted):
                    result = dispatcher.sync(
                        "sess_large", "/proj", audit_log,
                        "sndo_live_xxx", "https://api.example.com",
                    )

        assert result["synced"] == 12
        assert result["failed"] is False
        assert len(payload_sizes) > 1
        assert max(payload_sizes) < 5 * 1024 * 1024

    def test_413_splits_batch_and_single_oversize_does_not_block_later_events(self):
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState

        events = self._make_events(3)
        audit_log = MagicMock()
        audit_log.events = events
        dispatcher = CloudSyncDispatcher()
        sent = []

        def response_for(_sid, _root, batch, *_args, **_kwargs):
            sequences = [event.sequence for event in batch]
            sent.extend(sequences)
            if len(batch) > 1 or sequences == [1]:
                return "too_large", "payload_too_large", 5 * 1024 * 1024
            return "delivered", "HTTP 200", 200

        with patch.object(CloudSyncState, "get_cursor", return_value=0):
            with patch.object(CloudSyncState, "advance_cursor") as advance:
                with patch.object(dispatcher, "_post_batch", side_effect=response_for) as post:
                    result = dispatcher.sync(
                        "sess_413", "/proj", audit_log,
                        "sndo_live_xxx", "https://api.example.com",
                    )

        assert result["refused"] is True
        assert result["synced"] == 2
        assert result["pending"] == 1
        assert "sequence 1" in result["reason"]
        assert "bytes" in result["reason"]
        assert 2 in sent and 3 in sent
        advance.assert_called()
        assert post.call_args_list[-1].kwargs["force"] is True

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

    def test_sessionless_sync_is_skipped_and_session_sync_sends_events(self, tmp_path, monkeypatch):
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState

        monkeypatch.setattr("snodo.infrastructure.cloud_sync.resolve_home", lambda: tmp_path)
        events = self._make_events(3)
        for event in events:
            event.project_id = "github.com/example/project"
        audit = MagicMock(events=events)
        dispatcher = CloudSyncDispatcher()
        sent = []

        def accepted(_sid, _root, batch, *_args, **_kwargs):
            sent.extend(event.sequence for event in batch)
            return "delivered", "HTTP 200", 200

        with patch.object(dispatcher, "_post_batch", side_effect=accepted):
            first = dispatcher.sync("", "/project", audit, "key", "https://api.test")
            second = dispatcher.sync("sess_existing", "/project", audit, "key", "https://api.test")

        assert first["synced"] == 0
        assert first["skipped"] is True
        assert first["pending"] == 3
        assert second["synced"] == 3
        assert sent == [1, 2, 3]
        state = CloudSyncState()
        assert state.get_cursor("sess_existing") == 3

    def test_session_sync_then_sessionless_sync_never_posts_invalid_id(self, tmp_path, monkeypatch):
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher

        monkeypatch.setattr("snodo.infrastructure.cloud_sync.resolve_home", lambda: tmp_path)
        events = self._make_events(2)
        events[0].project_id = "github.com/example/project"
        events[1].project_id = "github.com/example/project"
        audit = MagicMock(events=events)
        dispatcher = CloudSyncDispatcher()
        with patch.object(dispatcher, "_post_batch", return_value=("delivered", "HTTP 200", 200)) as post:
            sent = dispatcher.sync("sess_existing", "/project", audit, "key", "https://api.test")
            sessionless = dispatcher.sync("", "/project", audit, "key", "https://api.test")

        assert sent["synced"] == 2
        assert sessionless["synced"] == 0
        assert sessionless["skipped"] is True
        post.assert_called_once()

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

    def test_429_body_retry_after_is_honoured_without_using_retry_budget(self):
        from unittest.mock import patch

        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher

        dispatcher = CloudSyncDispatcher()
        events = self._make_events(1)
        rate_limited = MagicMock()
        rate_limited.status_code = 429
        rate_limited.headers = {}
        rate_limited.text = '{"error":"rate_limited","retry_after":60}'
        rate_limited.json.return_value = {"error": "rate_limited", "retry_after": 60}
        ok_resp = MagicMock(status_code=200, text="ok")
        with (
            patch("httpx.post", side_effect=[rate_limited] * 7 + [ok_resp]) as mock_post,
            patch("snodo.infrastructure.cloud_sync.time.sleep") as mock_sleep,
        ):
            outcome, _reason, _status = dispatcher._post_batch(
                "sess_rl_body", "/proj", events,
                "sndo_live_xxx", "https://api.example.com",
            )

        assert outcome == "delivered"
        assert mock_post.call_count == 8
        assert mock_sleep.call_count == 7
        mock_sleep.assert_called_with(60)

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
            assert ev_payload["scope"] == "remote"
            assert ev_payload["event_type"] == "transition"

    def test_engine_batch_validates_against_ingest_envelope(self):
        """The payload assembled for ingest has a declared batch envelope."""
        from snodo.infrastructure.cloud_sync import AuditIngestBatch

        events = self._make_events(1)
        payload = {
            "session_id": "sess_envelope",
            "project_path": "/home/user/code/project",
            "display_name": "project",
            "events": [
                {
                    "sequence": events[0].sequence,
                    "timestamp": events[0].timestamp,
                    "event_type": events[0].event_type,
                    "project_id": (
                        events[0].project_id
                        if isinstance(events[0].project_id, str)
                        else ""
                    ),
                    "scope": "local",
                    "data": events[0].data,
                    "previous_hash": events[0].previous_hash,
                    "event_hash": events[0].event_hash,
                }
            ],
        }

        assert AuditIngestBatch.model_validate(payload).model_dump() == payload

    def test_post_batch_carries_scope_alongside_local_id(self):
        """A local: project_id is transmitted with scope 'local' so a consumer can tell it apart."""
        from unittest.mock import patch, MagicMock
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher

        dispatcher = CloudSyncDispatcher()
        events = self._make_events(1)
        events[0].project_id = "local:6bd1d012554546c4b9462bfaaa4183d8"

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
                "sess_local_scope",
                "/home/user/code/nfc-card-v2",
                events,
                "sndo_live_xxx",
                "https://api.example.com",
            )

        assert outcome == "delivered"
        assert captured_body is not None
        assert len(captured_body["events"]) == 1
        ev_payload = captured_body["events"][0]
        assert ev_payload["project_id"] == "local:6bd1d012554546c4b9462bfaaa4183d8"
        assert ev_payload["scope"] == "local"

    def test_post_batch_carries_wave_created_event_and_payload(self):
        """Transmitted cloud sync payload carries wave_created with wave_id and feature_description."""
        from unittest.mock import patch, MagicMock
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher
        from snodo.infrastructure.audit import AuditEvent

        dispatcher = CloudSyncDispatcher()
        ev = AuditEvent(
            sequence=1,
            timestamp="2026-09-17T12:00:00+00:00",
            event_type="wave_created",
            data={
                "op": "wave_created",
                "wave_id": "w_0001",
                "feature_description": "User Authentication System",
                "session_id": "sess_wave_test",
            },
            previous_hash="0000000000000000000000000000000000000000000000000000000000000000",
            event_hash="abcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            project_id="github.com/snodo-dev/test-repo",
        )

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
                "sess_wave_test",
                "/home/user/code/my-project",
                [ev],
                "sndo_live_xxx",
                "https://api.example.com",
            )

        assert outcome == "delivered"
        assert captured_body is not None
        assert len(captured_body["events"]) == 1
        payload_ev = captured_body["events"][0]
        assert payload_ev["event_type"] == "wave_created"
        assert payload_ev["data"]["wave_id"] == "w_0001"
        assert payload_ev["data"]["feature_description"] == "User Authentication System"
        assert "anchor_summaries" not in payload_ev["data"]
        assert "last_activity" not in payload_ev["data"]

    def test_v5_cloud_sends_compatible_prefix_and_holds_v6_chain_suffix(self):
        import time
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState
        from snodo.infrastructure.cloud_lease import CloudLease

        events = self._make_events(3)
        events[1].event_type = "task_merged"
        events[1].data.update({"plan_name": "p", "plan_wave": "w1", "commit_count": 3})
        events[2].event_type = "recon_started"
        captured = []
        lease = CloudLease("jti", "token", time.time() + 3600, interface_version=5)

        def accepted(_url, content, **_kwargs):
            captured.extend(json.loads(content)["events"])
            return MagicMock(status_code=200, text="ok")

        dispatcher = CloudSyncDispatcher()
        with (
            patch.object(CloudSyncState, "get_cursor", return_value=0),
            patch.object(CloudSyncState, "advance_cursor") as advance,
            patch.object(CloudSyncState, "record_attempt"),
            patch("snodo.infrastructure.cloud_lease.get_admission_lease", return_value=lease),
            patch("httpx.post", side_effect=accepted) as post,
        ):
            result = dispatcher.sync(
                "sess_v5", "/proj", MagicMock(events=events), "key", "https://api.test",
            )

        assert result["failed"] is True
        assert not result.get("refused")
        assert result["pending"] == 2
        assert [event["sequence"] for event in captured] == [1]
        assert captured[0]["event_hash"] == events[0].event_hash
        assert captured[0]["previous_hash"] == events[0].previous_hash
        assert captured[0]["data"] == events[0].data
        assert post.call_count == 1
        advance.assert_called_once_with("sess_v5", 1)

    def test_v5_payload_keeps_only_the_unchanged_compatible_prefix(self):
        from snodo.infrastructure.cloud_sync import _payload_for_events, _v5_payload

        events = self._make_events(3)
        events[1].event_type = "recon_started"
        payload = _payload_for_events("sess_v5_prefix", "/proj", events)

        projected = _v5_payload(payload, 5)

        assert projected is not None
        assert projected["events"] == payload["events"][:1]

    def test_v5_holds_task_unmerged_with_its_pinned_v6_shape(self):
        from snodo.infrastructure.cloud_sync import _payload_for_events, _v5_payload

        event = self._make_events(1)[0]
        event.event_type = "task_unmerged"
        event.data = {
            "task_ref": "1.1_x", "branch": "task/p/1.1_x", "reason": "manual",
            "session_id": "sess_v5", "plan_name": "p", "plan_wave": "1",
        }
        payload = _payload_for_events("sess_v5", "/proj", [event])
        assert _v5_payload(payload, 5) is None

    def test_v5_holds_plan_owned_disagreement_escalation(self):
        from snodo.infrastructure.cloud_sync import _payload_for_events, _v5_payload

        event = self._make_events(1)[0]
        event.event_type = "disagreement_escalated"
        event.data = {
            "task_ref": "1.1", "phase": "pre_execute", "policy": "unanimous",
            "plan_name": "release", "plan_wave": "2",
        }
        payload = _payload_for_events("sess_v5_escalation", "/proj", [event])
        assert _v5_payload(payload, 5) is None

    def test_v5_cloud_holds_batch_starting_with_v6_event(self):
        import time
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState
        from snodo.infrastructure.cloud_lease import CloudLease

        events = self._make_events(2)
        events[0].event_type = "recon_started"
        lease = CloudLease("jti", "token", time.time() + 3600, interface_version=5)
        dispatcher = CloudSyncDispatcher()

        with (
            patch.object(CloudSyncState, "get_cursor", return_value=0),
            patch.object(CloudSyncState, "advance_cursor") as advance,
            patch.object(CloudSyncState, "record_attempt"),
            patch("snodo.infrastructure.cloud_lease.get_admission_lease", return_value=lease),
            patch("httpx.post") as post,
        ):
            result = dispatcher.sync(
                "sess_v5_first_v6", "/proj", MagicMock(events=events),
                "key", "https://api.test",
            )

        assert result["failed"] is True
        assert "not yet advertised" in result["reason"]
        assert result["synced"] == 0
        assert result["pending"] == 2
        post.assert_not_called()
        advance.assert_not_called()

    def test_v6_cloud_sends_new_events_and_switches_after_v5(self):
        import time
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState
        from snodo.infrastructure.cloud_lease import CloudLease

        events = self._make_events(2)
        events[0].event_type = "transition"
        events[1].event_type = "recon_started"
        delivered = []
        lease_versions = iter((5, 5, 6))

        def lease_for_request(*_args, **_kwargs):
            version = next(lease_versions)
            return CloudLease("jti", "token", time.time() + 3600, interface_version=version)

        def accepted(_url, content, **_kwargs):
            delivered.extend(json.loads(content)["events"])
            return MagicMock(status_code=200, text="ok")

        dispatcher = CloudSyncDispatcher()
        cursor = {"value": 0}
        with (
            patch.object(CloudSyncState, "get_cursor", side_effect=lambda _sid: cursor["value"]),
            patch.object(CloudSyncState, "advance_cursor", side_effect=lambda _sid, seq: cursor.update(value=seq)),
            patch.object(CloudSyncState, "record_attempt"),
            patch("snodo.infrastructure.cloud_lease.get_admission_lease", side_effect=lease_for_request),
            patch("httpx.post", side_effect=accepted),
        ):
            first = dispatcher.sync(
                "sess_upgrade", "/proj", MagicMock(events=events), "key", "https://api.test",
            )
            second = dispatcher.sync(
                "sess_upgrade", "/proj", MagicMock(events=events), "key", "https://api.test",
            )

        assert first["failed"] is True
        assert first["synced"] == 1
        assert second["failed"] is False
        assert second["synced"] == 1
        assert cursor["value"] == 2
        assert [event["sequence"] for event in delivered] == [1, 2]
        assert delivered[1]["event_type"] == "recon_started"

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

    def test_sync_if_enabled_posts_to_ingest_base_even_with_tunnel_key(self):
        """The ingest/tunnel config split must not move audit sync.

        sync_if_enabled resolves the base from cloud.api_url (ingest);
        cloud.tunnel_api_url is for tunnel provisioning only.
        """
        import snodo.infrastructure.cloud_sync as cs
        from snodo.infrastructure.cloud_sync import sync_if_enabled

        config = {"cloud": {
            "sync_enabled": True,
            "api_key": "sndo_live_xxx",
            "api_url": "https://api.example.com",
            "lease_url": "https://app.example.com",
            "tunnel_api_url": "https://tunnel.example.com",
        }}

        with patch("snodo.infrastructure.cloud_sync.CloudSyncDispatcher.sync") as mock_sync:
            mock_sync.return_value = {"synced": 0, "failed": False, "pending": 0}
            sync_if_enabled("sess_split", "/proj", MagicMock(), config=config)

            import threading
            for t in threading.enumerate():
                if t is not threading.main_thread() and t.daemon:
                    t.join(timeout=1)

        mock_sync.assert_called_once()
        api_url_arg = mock_sync.call_args.args[4]
        assert api_url_arg == "https://api.example.com"
        assert "tunnel.example.com" not in api_url_arg
        assert mock_sync.call_args.kwargs["lease_url"] == "https://app.example.com"
        cs._pending_syncs.clear()

    def test_post_batch_targets_ingest_path(self):
        """_post_batch uses the minted jti in its ingest route."""
        from snodo.infrastructure.cloud_sync import CloudSyncDispatcher

        dispatcher = CloudSyncDispatcher()
        events = self._make_events(1)

        captured = {}

        def fake_post(url, **kwargs):
            captured["url"] = url
            resp = MagicMock()
            resp.status_code = 200
            resp.text = "ok"
            return resp

        with patch("httpx.post", side_effect=fake_post):
            outcome, _, _ = dispatcher._post_batch(
                "sess_ingest", "/proj", events,
                "sndo_live_xxx", "https://api.example.com",
            )

        assert outcome == "delivered"
        assert captured["url"] == "https://api.example.com/i/ls_test_lease"

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
    def test_sync_all_with_zero_sessions_skips_invalid_project_session_id(self):
        from snodo.cli.commands.cloud_cmd import cloud_sync_command

        audit = MagicMock(events=[MagicMock(project_id="github.com/example/project")])
        with (
            patch("snodo.infrastructure.audit.AuditLog", return_value=audit),
            patch("snodo.infrastructure.cloud_sync.CloudSyncDispatcher") as MockDisp,
            patch("snodo.infrastructure.session.SessionManager") as MockSM,
            patch("snodo.infrastructure.paths.require_project_root", return_value="/fake/proj"),
            patch("snodo.config.ConfigManager") as MockCM,
        ):
            MockCM.return_value.load.return_value = {
                "cloud": {"api_key": "sndo_live_xxx", "api_url": "https://api.example.com"},
            }
            MockSM.return_value.list_sessions.return_value = []
            MockDisp.return_value.sync.return_value = {"synced": 0, "failed": False, "pending": 0}

            assert cloud_sync_command(sync_all=True) == 0

        MockDisp.return_value.sync.assert_not_called()

    def test_refusal_after_partial_delivery_is_not_reported_as_success(self, capsys):
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
                                session = MagicMock(session_id="sess_partial", project_root="/fake/proj")
                                MockSM.return_value.get_active_session.return_value = session
                                MockDisp.return_value.sync.return_value = {
                                    "synced": 2, "failed": True, "refused": True,
                                    "reason": "HTTP 413: oversized single event", "pending": 1,
                                }
                                result = cloud_sync_command()

        output = capsys.readouterr().out
        assert result == 1
        assert "PARTIAL: 2 events synced" in output
        assert "1 event(s) pending" in output
        assert "✓" not in output

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
