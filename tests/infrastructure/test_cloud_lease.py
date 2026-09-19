"""Tests for the cloud lease admission mechanism (Fixes #376).

Pins the three non-negotiable admission properties:
1. Sending without a lease performs the exchange first, sending to a path
   containing the lease identifier and presenting the opaque bearer token.
2. A refusal at the exchange records a terminal refusal in CloudSyncState across
   processes and stops sending permanently.
3. An unreachable exchange results in silence (quiet backoff) rather than
   repeated attempts.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from snodo.infrastructure.cloud_lease import (
    reset_admission_state,
)
from snodo.infrastructure.cloud_liveness import _post_snapshot
from snodo.infrastructure.cloud_sync import CloudSyncDispatcher, CloudSyncState


@pytest.fixture(autouse=True)
def clean_lease_state(tmp_path):
    reset_admission_state()
    state_file = tmp_path / "cloud_sync.json"
    with patch.object(CloudSyncState, "__init__", lambda self, state_path=None: setattr(self, "_path", state_path or state_file)):
        yield state_file
    reset_admission_state()


class TestCloudAdmission:
    def test_sending_without_lease_performs_exchange_first(self, tmp_path):
        """Sending without a lease performs the exchange first and uses the path containing lease_id."""
        calls = []

        def mock_post(url, content=None, headers=None, **kwargs):
            calls.append(("POST", str(url), headers or {}, json.loads(content) if content else {}))
            if str(url).endswith("/lease"):
                resp = MagicMock(spec=httpx.Response)
                resp.status_code = 200
                resp.json.return_value = {
                    "lease_id": "ls_test_fixed_length_12345",
                    "token": "opaque_bearer_secret_xyz",
                    "expires_in": 300,
                }
                return resp
            if "/ingest/ls_test_fixed_length_12345" in str(url):
                resp = MagicMock(spec=httpx.Response)
                resp.status_code = 200
                resp.text = "ok"
                return resp
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 404
            resp.text = "not found"
            return resp

        dispatcher = CloudSyncDispatcher()
        event = MagicMock(sequence=1, timestamp="2026-09-19T00:00:00Z", event_type="transition",
                          project_id="local:p1", data={}, previous_hash="", event_hash="abc")

        with patch("httpx.post", side_effect=mock_post):
            outcome, reason, status = dispatcher._post_batch(
                session_id="sess_alpha",
                project_root=str(tmp_path),
                batch=[event],
                api_key="sndo_live_mykey123",
                api_url="https://api.snodo.test",
            )

        assert outcome == "delivered"
        assert len(calls) == 2

        # First call: exchange
        method1, url1, headers1, body1 = calls[0]
        assert method1 == "POST"
        assert url1 == "https://api.snodo.test/lease"
        assert headers1.get("Authorization") == "Bearer sndo_live_mykey123"

        # Second call: ingest with lease identifier in path and bearer token
        method2, url2, headers2, body2 = calls[1]
        assert method2 == "POST"
        assert url2 == "https://api.snodo.test/ingest/ls_test_fixed_length_12345"
        assert headers2.get("Authorization") == "Bearer opaque_bearer_secret_xyz"

    def test_refusal_at_exchange_stops_sending_permanently(self, tmp_path):
        """A refusal at the exchange records a terminal refusal and permanently halts sending."""
        calls = []

        def mock_post(url, **kwargs):
            calls.append(str(url))
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 401
            resp.text = "Unauthorized: API key revoked"
            return resp

        dispatcher = CloudSyncDispatcher()
        event = MagicMock(sequence=1, timestamp="2026-09-19T00:00:00Z", event_type="transition",
                          project_id="local:p1", data={}, previous_hash="", event_hash="abc")

        with patch("httpx.post", side_effect=mock_post):
            outcome, reason, status = dispatcher._post_batch(
                session_id="sess_refused",
                project_root=str(tmp_path),
                batch=[event],
                api_key="sndo_live_badkey",
                api_url="https://api.snodo.test",
            )

        assert outcome == "refused"
        assert len(calls) == 1
        assert calls[0] == "https://api.snodo.test/lease"

        # Refusal is persisted in CloudSyncState
        state = CloudSyncState()
        assert state.is_refused("sess_refused") is True

        # Subsequent send attempts are stopped permanently without making any network calls
        calls.clear()
        with patch("httpx.post", side_effect=mock_post):
            outcome2, _, _ = dispatcher._post_batch(
                session_id="sess_refused",
                project_root=str(tmp_path),
                batch=[event],
                api_key="sndo_live_badkey",
                api_url="https://api.snodo.test",
            )

        assert outcome2 == "refused"
        assert len(calls) == 0  # Permanent stop: zero network calls made

    def test_unreachable_exchange_results_in_silence_rather_than_repeated_attempts(self, tmp_path):
        """An unreachable exchange enters a quiet backoff window resulting in silence."""
        calls = []

        def mock_post(url, **kwargs):
            calls.append(str(url))
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 503
            resp.text = "Service Unavailable"
            return resp

        dispatcher = CloudSyncDispatcher()
        event = MagicMock(sequence=1, timestamp="2026-09-19T00:00:00Z", event_type="transition",
                          project_id="local:p1", data={}, previous_hash="", event_hash="abc")

        with patch("httpx.post", side_effect=mock_post):
            outcome1, _, _ = dispatcher._post_batch(
                session_id="sess_unreachable",
                project_root=str(tmp_path),
                batch=[event],
                api_key="sndo_live_key",
                api_url="https://api.snodo.test",
            )

        assert outcome1 == "retryable"
        assert len(calls) == 1
        assert calls[0] == "https://api.snodo.test/lease"

        # A second send attempt while in the quiet window must be completely silent (0 network calls)
        calls.clear()
        with patch("httpx.post", side_effect=mock_post):
            outcome2, _, _ = dispatcher._post_batch(
                session_id="sess_unreachable",
                project_root=str(tmp_path),
                batch=[event],
                api_key="sndo_live_key",
                api_url="https://api.snodo.test",
            )

        assert outcome2 == "retryable"
        assert len(calls) == 0  # Silence: no network call attempted

    def test_liveness_sender_uses_lease_and_path(self, tmp_path):
        """Cloud liveness also exchanges for a lease and includes lease_id in its path."""
        calls = []

        def mock_post(url, content=None, headers=None, **kwargs):
            calls.append(("POST", str(url), headers or {}))
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 200
            resp.json.return_value = {
                "lease_id": "ls_live_fixed_98765",
                "token": "tok_live_bearer",
                "expires_in": 300,
            }
            return resp

        def mock_put(url, content=None, headers=None, **kwargs):
            calls.append(("PUT", str(url), headers or {}))
            resp = MagicMock(spec=httpx.Response)
            resp.status_code = 204
            resp.text = ""
            return resp

        snapshot = {
            "session_id": "sess_live_1",
            "project_id": "local:p1",
            "scope": "local",
            "display_name": "demo",
            "run_started_at": None,
            "plans": [],
            "tasks": [],
            "jobs": [],
            "task_status_counts": {},
            "job_status_counts": {},
            "last_event": None,
            "last_activity_at": None,
            "snapshot_at": "2026-09-19T00:00:00Z",
        }
        config = {
            "cloud": {
                "sync_enabled": True,
                "api_key": "sndo_live_key123",
                "api_url": "https://api.snodo.test",
                "liveness_url": "https://app.snodo.test/v1",
            },
        }

        with patch("httpx.post", side_effect=mock_post), patch("httpx.put", side_effect=mock_put):
            _post_snapshot(snapshot, config=config)

        assert len(calls) == 2
        assert calls[0][0] == "POST"
        assert calls[0][1] == "https://api.snodo.test/lease"
        assert calls[0][2].get("Authorization") == "Bearer sndo_live_key123"

        assert calls[1][0] == "PUT"
        assert calls[1][1] == "https://app.snodo.test/v1/live/sess_live_1/ls_live_fixed_98765"
        assert calls[1][2].get("Authorization") == "Bearer tok_live_bearer"
