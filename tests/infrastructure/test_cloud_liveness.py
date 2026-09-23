"""Tests for the cloud liveness push (Fixes #291).

Liveness is event-driven, throttled, full-snapshot, drop-on-failure, keyed by
session, and it never carries a user identifier or anything the emit contract
refuses. These tests pin each of those properties against
``snodo.infrastructure.cloud_liveness`` — and pin the one property the
separation exists for: a failed liveness push is dropped, and it never
disturbs the ingest cursor.
"""

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from snodo.infrastructure import cloud_liveness

_TEST_CONFIG = {
    "cloud": {
        "sync_enabled": True,
        "api_key": "sndo_live_testkey",
        "api_url": "https://api.snodo.test",
    },
}


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


@pytest.fixture
def project(tmp_path):
    """A project tree with one session, one begun plan, and a running task."""
    root = tmp_path / "myproj"
    _write(root / ".snodo" / "project.json",
           {"project.id": "local:test123", "scope": "local"})
    _write(root / ".snodo" / "state.json", {
        "current_mode": "producer",
        "active_session": {"producer": "sess_test_1"},
    })
    home = tmp_path / "home"
    _write(home / "sessions" / "sess_test_1.json", {
        "session_id": "sess_test_1",
        "mode": "producer",
        "project_root": str(root),
        "project_id": "local:test123",
        "created_at": "2026-09-02T21:10:04+00:00",
        "updated_at": "2026-09-02T21:10:04+00:00",
        "checkpoint": {"current_task": "2.1", "decisions": {},
                       "memory_summary": "", "timestamp": ""},
    })
    _write(root / ".snodo" / "plans" / "wave8" / "status.json", {
        "tasks": {"2.1": "in_progress", "2.2": "pending"},
    })
    _write(root / ".snodo" / "tasks" / "t_alpha" / "state.json", {
        "task_id": "t_alpha", "status": "running",
        "started_at": 1787000000.0,
    })

    with patch.object(cloud_liveness, "resolve_home", lambda: home):
        yield root, home


class _Posts:
    """Records httpx.put calls; blocks each one until released (or not)."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append((url, json.loads(kwargs["content"])))
        return type("R", (), {"status_code": 204, "text": ""})()


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    """Poll *predicate* until true or *timeout*; real timers, so no fixed sleep."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def _no_floor(*_args, **_kwargs):
    """Disable the floor timer for tests about the ceiling alone."""
    return None


@pytest.fixture(autouse=True)
def _sync_enabled():
    """Sync on via the config gate, clean throttle bookkeeping per test.

    ``ConfigManager`` is patched, not the gate: every real path into
    ``_should_sync`` and ``_post_snapshot`` loads config through it, so the
    tests exercise the same gate production uses. ``uninstall`` runs on entry
    as well as exit: another test in the same worker may have armed the
    listener through ``_execute_task``, and this file's inert-listener test
    depends on a disarmed start.
    """
    from snodo.infrastructure import cloud_lease

    cloud_liveness.uninstall()
    cloud_lease.reset_admission_state()
    test_lease = cloud_lease.CloudLease(
        lease_id="ls_test_lease",
        token="tok_test_lease",
        expires_at=time.time() + 3600,
    )
    with patch.object(cloud_lease, "get_current_lease", return_value=test_lease), \
            patch.object(cloud_lease, "get_admission_lease", return_value=test_lease), \
            patch("snodo.config.ConfigManager") as mock_cm:
        mock_cm.return_value.load.return_value = _TEST_CONFIG
        cloud_liveness.reset_liveness_state()
        yield mock_cm
    cloud_liveness.reset_liveness_state()
    cloud_liveness.uninstall()
    cloud_lease.reset_admission_state()


# ------------------------------------------------------------------ #
# Change-driven, with a floor while something is running (Fixes #323)
# ------------------------------------------------------------------ #


class TestTransitionDriven:
    def test_state_is_pushed_on_transition(self, project):
        root, _ = project
        posts = _Posts()
        with patch("httpx.put", posts):
            assert cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()

        assert len(posts.calls) == 1
        url, body = posts.calls[0]
        assert url == "https://app.snodo.test/v1/live/sess_test_1/ls_test_lease"
        assert body["session_id"] == "sess_test_1"
        assert body["project_id"] == "local:test123"
        assert body["scope"] == "local"
        # The plan arrives as shape, not as a task dump: a begun plan node
        # carries its status counts and the detail of what has started.
        # (No plan.yml here: the task detail rides on the plan node.)
        assert body["plans"] == [{
            "name": "wave8",
            "total": 2,
            "status_counts": {"in_progress": 1, "pending": 1},
            "tasks": [{"id": "2.1", "status": "in_progress"}],
        }]
        assert body["tasks"][0]["id"] == "t_alpha"
        assert body["tasks"][0]["status"] == "running"
        assert body["run_started_at"] == "2026-09-02T21:10:04+00:00"
        assert "snapshot_at" in body

    def test_running_session_is_pushed_each_interval_without_a_transition(self, project):
        """Time passing while running is now enough: a quiet run is heard.

        A coder reading for twenty-four minutes produces no transition, so
        the wire used to go silent and look exactly like a dead machine. The
        floor sends the same true snapshot once per interval (Fixes #323).
        """
        root, _ = project
        posts = _Posts()
        with patch("httpx.put", posts), \
                patch.object(cloud_liveness, "LIVENESS_THROTTLE_SECONDS", 0.05):
            assert cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
            assert len(posts.calls) == 1
            assert _wait_until(lambda: len(posts.calls) > 1), \
                "a running session was not heard from again without a transition"
            # The ceiling holds too: no second push inside the interval.
            settled = len(posts.calls)
            time.sleep(0.01)
            assert len(posts.calls) == settled

    def test_engine_transition_event_triggers_a_push(self, project):
        """An audit append of a trigger event pushes; a non-trigger does not."""
        root, _ = project
        posts = _Posts()
        from snodo.infrastructure.audit import AuditLog
        log = AuditLog(str(root / ".snodo" / "audit.log"), project_id="local:test123")
        with patch("httpx.put", posts):
            cloud_liveness.install()
            log.append_event("dispatch", {"task_ref": "t_alpha",
                                          "session_id": "sess_test_1"})
            cloud_liveness.wait_for_pushes()
            assert len(posts.calls) == 1

            # ~75% of all events are this kind of restatement: not liveness.
            log.append_event("session_decision_updated",
                             {"key": "halt", "value": {}})
            cloud_liveness.wait_for_pushes()
            assert len(posts.calls) == 1

    def test_listener_inert_until_installed(self, project):
        """A utility process appending one audit event is not a run."""
        root, _ = project
        posts = _Posts()
        from snodo.infrastructure.audit import AuditLog
        log = AuditLog(str(root / ".snodo" / "audit.log"), project_id="local:test123")
        with patch("httpx.put", posts):
            log.append_event("task_complete", {"task_ref": "t_alpha",
                                               "session_id": "sess_test_1"})
            cloud_liveness.wait_for_pushes()
        assert posts.calls == []

    def test_sync_disabled_makes_no_network_call(self, project):
        root, _ = project
        disabled = {"cloud": {"sync_enabled": False, "api_key": "sndo_live_x"}}
        posts = _Posts()
        from snodo.infrastructure.audit import AuditLog
        with patch("snodo.config.ConfigManager") as mock_cm:
            mock_cm.return_value.load.return_value = disabled
            cloud_liveness.reset_liveness_state()
            cloud_liveness.install()
            log = AuditLog(str(root / ".snodo" / "audit.log"),
                           project_id="local:test123")
            with patch("httpx.put", posts), patch("httpx.post") as mock_post:
                cloud_liveness.request_liveness_push("sess_test_1", str(root))
                log.append_event("dispatch", {"task_ref": "t_alpha",
                                              "session_id": "sess_test_1"})
                cloud_liveness.note_transition(str(root), session_id="sess_test_1")
                cloud_liveness.wait_for_pushes()
            assert posts.calls == []
            mock_post.assert_not_called()


# ------------------------------------------------------------------ #
# Throttle: a burst coalesces into one push carrying the later state
# ------------------------------------------------------------------ #


class TestThrottle:
    def test_two_transitions_in_window_produce_one_push_with_later_state(self, project):
        """The in-flight push absorbs the second transition and carries its state.

        The first push's worker is held at the door of snapshot-build while
        the second transition lands and requests a push: the burst must be
        one wire write — the one already in flight — and it must carry the
        later state, because the snapshot is built at send time, not at
        trigger time.
        """
        root, _ = project
        posts = _Posts()
        real_build = cloud_liveness.build_liveness_snapshot
        at_build = threading.Event()
        release = threading.Event()

        def gated_build(*args, **kwargs):
            at_build.set()
            assert release.wait(timeout=5)
            return real_build(*args, **kwargs)

        with patch("httpx.put", posts), \
                patch.object(cloud_liveness, "build_liveness_snapshot", gated_build):
            assert cloud_liveness.request_liveness_push("sess_test_1", str(root))
            assert at_build.wait(timeout=5)
            # Second transition, inside the window: 2.1 finished, 2.2 begun.
            _write(root / ".snodo" / "plans" / "wave8" / "status.json", {
                "tasks": {"2.1": "completed", "2.2": "in_progress"},
            })
            assert not cloud_liveness.request_liveness_push("sess_test_1", str(root))
            release.set()
            cloud_liveness.wait_for_pushes()

        assert len(posts.calls) == 1
        _, body = posts.calls[0]
        statuses = {t["id"]: t["status"] for t in body["plans"][0]["tasks"]}
        assert statuses == {"2.1": "completed", "2.2": "in_progress"}

    def test_requests_inside_the_window_are_dropped(self, project):
        root, _ = project
        posts = _Posts()
        with patch("httpx.put", posts):
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
            assert not cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
        assert len(posts.calls) == 1

    def test_terminal_transition_bypasses_the_window(self, project):
        """A dropped terminal push would strand a false "running" until the
        next event happened to displace it — so it forces past the throttle."""
        root, _ = project
        posts = _Posts()
        with patch("httpx.put", posts):
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
            assert len(posts.calls) == 1
            assert cloud_liveness.request_liveness_push(
                "sess_test_1", str(root), force=True,
            )
            cloud_liveness.wait_for_pushes()
        assert len(posts.calls) == 2

    def test_window_reopens_after_throttle_seconds(self, project):
        root, _ = project
        posts = _Posts()
        with patch("httpx.put", posts), \
                patch.object(cloud_liveness, "_schedule_floor", _no_floor), \
                patch.object(cloud_liveness, "LIVENESS_THROTTLE_SECONDS", 0.05):
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
            time.sleep(0.06)
            assert cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
        assert len(posts.calls) == 2

    def test_planner_status_write_pushes_without_an_audit_event(self, project):
        """update_status changes what is running and appends no event; the
        planner reports the change through note_transition."""
        root, _ = project
        posts = _Posts()
        with patch("httpx.put", posts):
            cloud_liveness.note_transition(str(root))  # session from the pointer
            cloud_liveness.wait_for_pushes()
        assert len(posts.calls) == 1


# ------------------------------------------------------------------ #
# The interval is also a floor while something is running (Fixes #323)
# ------------------------------------------------------------------ #


class TestFloor:
    def test_a_busy_session_does_not_exceed_the_interval_rate(self, project):
        """The floor adds nothing to a session already pushing at the
        interval: transitions coalesce under the same ceiling."""
        root, _ = project
        posts = _Posts()
        interval = 0.05
        with patch("httpx.put", posts), \
                patch.object(cloud_liveness, "LIVENESS_THROTTLE_SECONDS", interval):
            start = time.monotonic()
            for _ in range(20):
                cloud_liveness.request_liveness_push("sess_test_1", str(root))
                time.sleep(0.005)
                cloud_liveness.wait_for_pushes()
            elapsed = time.monotonic() - start
        # Twenty requests; one push per interval is about elapsed/interval.
        assert len(posts.calls) < 20
        assert len(posts.calls) <= elapsed / interval + 2

    def test_a_settled_session_is_not_heard_from_again(self, project):
        """The terminal push is the last word: once nothing runs, the floor
        stops instead of repeating a finished snapshot as a heartbeat."""
        root, _ = project
        _write(root / ".snodo" / "tasks" / "t_alpha" / "state.json", {
            "task_id": "t_alpha", "status": "completed",
            "started_at": 1787000000.0,
        })
        _write(root / ".snodo" / "plans" / "wave8" / "status.json", {
            "tasks": {"2.1": "completed"},
        })
        posts = _Posts()
        with patch("httpx.put", posts), \
                patch.object(cloud_liveness, "LIVENESS_THROTTLE_SECONDS", 0.05):
            assert cloud_liveness.request_liveness_push(
                "sess_test_1", str(root), force=True,
            )
            cloud_liveness.wait_for_pushes()
            assert len(posts.calls) == 1
            time.sleep(0.15)  # three intervals
            cloud_liveness.wait_for_pushes()
        assert len(posts.calls) == 1

    def test_the_configured_interval_drives_the_floor(self, project):
        """A deployment lowers ``cloud.liveness_interval_seconds`` to hear
        from a quiet run more often; the default stays sixty seconds."""
        root, _ = project
        posts = _Posts()
        config = {
            "cloud": {
                "sync_enabled": True,
                "api_key": "sndo_live_testkey",
                "api_url": "https://api.snodo.test",
                "liveness_interval_seconds": 0.05,
            },
        }
        with patch("snodo.config.ConfigManager") as mock_cm, patch("httpx.put", posts):
            mock_cm.return_value.load.return_value = config
            cloud_liveness.reset_liveness_state()
            assert cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
            assert len(posts.calls) == 1
            assert _wait_until(lambda: len(posts.calls) > 1), \
                "the configured interval was not used for the floor"

    def test_absent_or_unreadable_interval_falls_back_to_sixty(self, project):
        assert cloud_liveness._interval_seconds({"cloud": {}}) == 60.0
        assert cloud_liveness._interval_seconds({
            "cloud": {"liveness_interval_seconds": "nonsense"},
        }) == 60.0
        assert cloud_liveness._interval_seconds({
            "cloud": {"liveness_interval_seconds": 0},
        }) == 60.0
        assert cloud_liveness._interval_seconds({
            "cloud": {"liveness_interval_seconds": 5},
        }) == 5.0


# ------------------------------------------------------------------ #
# A session with nothing running sends nothing
# ------------------------------------------------------------------ #


class TestIdleSession:
    def test_nothing_running_pushes_nothing(self, tmp_path):
        root = tmp_path / "idleproj"
        _write(root / ".snodo" / "plans" / "p1" / "status.json",
               {"tasks": {"1.1": "pending"}})
        posts = _Posts()
        with patch("httpx.put", posts):
            cloud_liveness.request_liveness_push("sess_idle", str(root))
            cloud_liveness.wait_for_pushes()
        assert posts.calls == []

    def test_idle_session_never_writes_the_sync_state_file(self, tmp_path):
        root = tmp_path / "idleproj"
        (root / ".snodo").mkdir(parents=True)
        posts = _Posts()
        with patch("httpx.put", posts):
            cloud_liveness.note_transition(str(root), session_id="sess_idle")
            cloud_liveness.wait_for_pushes()
        assert posts.calls == []

    def test_idle_session_is_never_heard_from_over_time(self, tmp_path):
        """Nothing running means nothing sent, interval after interval: the
        floor is gated by "running", not by the clock (Fixes #323)."""
        root = tmp_path / "idleproj"
        _write(root / ".snodo" / "plans" / "p1" / "status.json",
               {"tasks": {"1.1": "pending"}})
        posts = _Posts()
        with patch("httpx.put", posts), \
                patch.object(cloud_liveness, "LIVENESS_THROTTLE_SECONDS", 0.05):
            cloud_liveness.request_liveness_push("sess_idle", str(root))
            cloud_liveness.wait_for_pushes()
            time.sleep(0.15)  # three intervals
            cloud_liveness.wait_for_pushes()
        assert posts.calls == []

    def test_past_audit_history_alone_is_not_liveness(self, tmp_path):
        """Events from a finished run must not make an idle tree push."""
        root = tmp_path / "quietproj"
        (root / ".snodo").mkdir(parents=True)
        (root / ".snodo" / "audit.log").write_text(json.dumps({
            "sequence": 1, "timestamp": "2026-09-01T00:00:00+00:00",
            "event_type": "task_complete", "data": {"task_ref": "old"},
        }) + "\n")
        assert cloud_liveness.build_liveness_snapshot("sess_old", str(root)) is None


# ------------------------------------------------------------------ #
# Failure is a drop, not a queue — and it never touches the cursor
# ------------------------------------------------------------------ #


class TestFailureIsDrop:
    def test_failed_push_is_not_retried(self, project):
        root, _ = project
        calls = []

        def failing_put(url, **kwargs):
            calls.append(url)
            raise OSError("unreachable")

        with patch("httpx.put", failing_put):
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
        assert len(calls) == 1  # exactly one attempt; no retry loop

    def test_non_2xx_is_not_retried(self, project):
        root, _ = project
        calls = []

        def server_error(url, **kwargs):
            calls.append(url)
            return type("R", (), {"status_code": 500, "text": "nope"})

        with patch("httpx.put", server_error):
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
        assert len(calls) == 1

    def test_failure_does_not_disturb_the_ingest_cursor(self, project, tmp_path):
        """The whole point of the separate wire: a dropped liveness push
        leaves the history cursor exactly where it was."""
        root, _ = project
        from snodo.infrastructure import cloud_sync

        session_id = "sess_test_1"
        state_path = tmp_path / "cloud_sync.json"
        state = cloud_sync.CloudSyncState(state_path=state_path)
        state.advance_cursor(session_id, 42)
        before = json.loads(state_path.read_text())

        def failing_put(url, **kwargs):
            raise OSError("unreachable")

        with patch("httpx.put", failing_put):
            cloud_liveness.request_liveness_push(session_id, str(root))
            cloud_liveness.wait_for_pushes()
            cloud_liveness.request_liveness_push(session_id, str(root), force=True)
            cloud_liveness.wait_for_pushes()

        assert cloud_sync.CloudSyncState(state_path=state_path).get_cursor(session_id) == 42
        assert json.loads(state_path.read_text()) == before

    def test_next_transition_supersedes_the_dropped_push(self, project):
        """Nothing queued to replay: the failed push is simply gone, and the
        next transition carries current truth (never stale state)."""
        root, _ = project
        posts = _Posts()
        attempts = []

        def flaky_put(url, **kwargs):
            attempts.append(url)
            if len(attempts) == 1:
                raise OSError("unreachable")
            return posts(url, **kwargs)

        _write(root / ".snodo" / "plans" / "wave8" / "status.json", {
            "tasks": {"2.1": "completed", "2.2": "in_progress"},
        })
        with patch("httpx.put", flaky_put), \
                patch.object(cloud_liveness, "_schedule_floor", _no_floor), \
                patch.object(cloud_liveness, "LIVENESS_THROTTLE_SECONDS", 0.0):
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
            assert posts.calls == []  # the failed push is dropped, not held
            # The next scheduled push is delayed after a transient failure;
            # advance the test seam rather than retrying it immediately.
            cloud_liveness._sessions["sess_test_1"]["blocked_until"] = 0
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()

        assert len(posts.calls) == 1
        _, body = posts.calls[0]
        statuses = {t["id"]: t["status"] for t in body["plans"][0]["tasks"]}
        assert statuses == {"2.1": "completed", "2.2": "in_progress"}


# ------------------------------------------------------------------ #
# What is on the wire
# ------------------------------------------------------------------ #


class TestPayloadContent:
    def test_no_user_identifier_appears_in_the_payload(self, project):
        root, _ = project
        posts = _Posts()
        with patch("httpx.put", posts):
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
        assert len(posts.calls) == 1
        _, body = posts.calls[0]
        flat = json.dumps(body).lower()
        for token in ("user", "owner", "email", "author", "operator",
                      "api_key", "token", "sndo_live"):
            assert token not in flat, f"{token!r} leaked into the payload"
        # Identity is session + project only.
        assert set(body) == {
            "session_id", "project_id", "scope", "display_name",
            "run_started_at", "plans", "tasks", "jobs",
            "task_status_counts", "job_status_counts",
            "last_event", "last_activity_at", "snapshot_at",
        }

    def test_payloads_and_paths_stay_on_the_machine(self, project):
        root, _ = project
        _write(root / ".snodo" / "tasks" / "t_alpha" / "state.json", {
            "task_id": "t_alpha", "status": "running",
            "started_at": 1787000000.0,
            "description": "the whole task spec",
            "halt": {"reason": "validator justification quoting file contents"},
            # A cost that cannot occur as a substring of an ISO timestamp: the
            # leak check below is a substring search over the serialised body,
            # and 0.25 matched seconds like "50.251975".
            "usage": [{"model": "claude", "cost": 987654.321, "prompt": "secret"}],
        })
        posts = _Posts()
        with patch("httpx.put", posts):
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
        _, body = posts.calls[0]
        assert body["tasks"][0]["id"] == "t_alpha"
        assert body["tasks"][0]["status"] == "running"
        assert body["tasks"][0]["started_at"] == datetime.fromtimestamp(
            1787000000.0, timezone.utc,
        ).isoformat()
        flat = json.dumps(body)
        for leak in ("secret", "validator justification", "the whole task spec",
                     str(root), "987654.321", "usage"):
            assert leak not in flat

    def test_last_event_and_timestamp_come_from_the_audit_tail(self, project):
        root, _ = project
        with open(root / ".snodo" / "audit.log", "w") as f:
            f.write(json.dumps({
                "sequence": 7, "timestamp": "2026-09-02T21:50:01+00:00",
                "event_type": "dispatch", "data": {"task_ref": "t_alpha"},
            }) + "\n")
        posts = _Posts()
        with patch("httpx.put", posts):
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
        _, body = posts.calls[0]
        assert body["last_event"] == {
            "event_type": "dispatch", "timestamp": "2026-09-02T21:50:01+00:00",
        }

    def test_auth_is_the_personal_api_key_in_the_header_only(self, project):
        root, _ = project
        posts = _Posts()
        with patch("httpx.put", posts):
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
        url, body = posts.calls[0]
        assert url.startswith("https://app.snodo.test/v1/live/")
        assert "sndo_live_testkey" not in url
        assert "sndo_live_testkey" not in json.dumps(body)

    def test_put_carries_the_bearer_header(self, project):
        root, _ = project
        seen = {}

        def put(url, **kwargs):
            seen.update(headers=kwargs.get("headers") or {})
            return type("R", (), {"status_code": 204, "text": ""})

        with patch("httpx.put", put):
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
        assert seen["headers"]["Authorization"] == "Bearer tok_test_lease"

    def test_jobs_are_reported_with_status(self, project):
        root, _ = project
        _write(root / ".snodo" / "jobs" / "j_20260902_a1" / "state.json", {
            "job_id": "j_20260902_a1", "status": "running",
            "started_at": 1787000001.0, "stdout": "unbounded tool output",
        })
        _write(root / ".snodo" / "jobs" / "notajob" / "state.json", {"status": "x"})
        posts = _Posts()
        with patch("httpx.put", posts):
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
        _, body = posts.calls[0]
        assert [j["id"] for j in body["jobs"]] == ["j_20260902_a1"]
        assert body["jobs"][0]["status"] == "running"
        assert "unbounded tool output" not in json.dumps(body)

    def test_task_ref_links_a_job_to_its_plan_task(self, project):
        """A job nested under its task keeps its identity off the top level.

        ``task.json`` is read for the task id only: the description (the task
        spec) stays on the machine with the payload fields.
        """
        root, _ = project
        (root / ".snodo" / "plans" / "wave8" / "plan.yml").write_text(
            "waves:\n  - id: 1\n    tasks: ['2.1']\n"
        )
        _write(root / ".snodo" / "jobs" / "j_20260902_a2" / "state.json", {
            "status": "running", "started_at": 1787000001.0,
        })
        _write(root / ".snodo" / "jobs" / "j_20260902_a2" / "task.json", {
            "task_id": "2.1", "description": "the whole task spec",
        })
        body = cloud_liveness.build_liveness_snapshot("sess_test_1", str(root))
        task = body["plans"][0]["waves"][0]["tasks"][0]
        assert task["id"] == "2.1"
        assert task["jobs"] == [{
            "id": "j_20260902_a2", "status": "running",
            "started_at": datetime.fromtimestamp(1787000001.0, timezone.utc).isoformat(),
        }]
        # The nested job is not also enumerated at the top level.
        assert body["jobs"] == []
        assert "the whole task spec" not in json.dumps(body)

    def test_snapshot_carries_no_delta_semantics(self, project):
        """Two pushes are two full snapshots, not a base and a diff."""
        root, _ = project
        posts = _Posts()
        with patch("httpx.put", posts), \
                patch.object(cloud_liveness, "_schedule_floor", _no_floor), \
                patch.object(cloud_liveness, "LIVENESS_THROTTLE_SECONDS", 0.0):
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
            _write(root / ".snodo" / "tasks" / "t_alpha" / "state.json", {
                "task_id": "t_alpha", "status": "completed",
                "started_at": 1787000000.0,
            })
            cloud_liveness.request_liveness_push("sess_test_1", str(root))
            cloud_liveness.wait_for_pushes()
        assert len(posts.calls) == 2
        _, first = posts.calls[0]
        _, second = posts.calls[1]
        assert first["tasks"][0]["status"] == "running"
        # The settled record leaves the live lists and joins the tally: same
        # truth, carried as a count instead of an enumeration.
        assert second["tasks"] == []
        assert second["task_status_counts"] == {"completed": 1}
        # The second snapshot is self-contained: same shape, all sections.
        assert set(first) == set(second)


# ------------------------------------------------------------------ #
# The snapshot carries the plan's shape, not its history (Fixes #303)
# ------------------------------------------------------------------ #


def _make_plan(root: Path, name: str, plan_yml: str, status: dict) -> None:
    plan_dir = root / ".snodo" / "plans" / name
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "plan.yml").write_text(plan_yml)
    _write(plan_dir / "status.json", {"tasks": status})


def _make_job(root: Path, job_id: str, status: str, task_ref: str,
              started: float) -> None:
    job_dir = root / ".snodo" / "jobs" / job_id
    _write(job_dir / "state.json", {"status": status, "started_at": started})
    _write(job_dir / "task.json", {"task_id": task_ref, "description": "spec"})


def _make_task(root: Path, task_id: str, status: str, wave_id: str = "") -> None:
    state = {"status": status, "started_at": 1787000000.0}
    if wave_id:
        state["wave_id"] = wave_id
    _write(root / ".snodo" / "tasks" / task_id / "state.json", state)


def _snapshot(root: Path, session_id: str = "sess_shape") -> dict:
    """Build against an empty home: shape tests never read a real session."""
    with patch.object(
        cloud_liveness, "resolve_home",
        lambda: root.parent / "nohome",
    ):
        return cloud_liveness.build_liveness_snapshot(session_id, str(root))


_WAVES_YML = """
waves:
  - id: 1
    depends_on: []
    tasks: ['1.1', '1.2', '1.3']
  - id: 2
    depends_on: [1]
    tasks: ['2.1', '2.2', '2.3']
"""


def _shaped_project(tmp_path: Path, attempts_per_completed_task: int) -> Path:
    """One incomplete plan: wave 1 fully completed, wave 2 at the frontier.

    ``attempts_per_completed_task`` multiplies the *settled job history* under
    the completed wave while leaving the plan's shape untouched — exactly the
    twelve-day session that must stop re-shipping its accumulation.
    """
    root = tmp_path / "shaped"
    _make_plan(root, "wave8", _WAVES_YML, {
        "1.1": "completed", "1.2": "completed", "1.3": "completed",
        "2.1": "completed", "2.2": "in_progress", "2.3": "pending",
    })
    for wave_task, wave_status in (("1.1", "completed"), ("1.2", "completed"),
                                   ("1.3", "completed"), ("2.1", "completed")):
        _write(root / ".snodo" / "tasks" / wave_task / "state.json", {
            "status": "completed", "started_at": 1787000000.0,
        })
        for n in range(attempts_per_completed_task):
            _make_job(root, f"j_done_{wave_task}_{n}", "completed", wave_task,
                      1787000000.0 + n)
    _write(root / ".snodo" / "tasks" / "2.2" / "state.json", {
        "status": "running", "started_at": 1787000200.0,
    })
    _make_job(root, "j_live_22", "running", "2.2", 1787000200.0)
    return root


class TestSnapshotShape:
    def test_completed_wave_is_counts_running_wave_is_detail(self, tmp_path):
        root = _shaped_project(tmp_path, attempts_per_completed_task=2)
        body = _snapshot(root)
        plan = body["plans"][0]
        assert plan["name"] == "wave8"
        assert plan["total"] == 6
        assert plan["status_counts"] == {
            "completed": 4, "in_progress": 1, "pending": 1,
        }

        wave1, wave2 = plan["waves"]
        # The completed branch: a count and the summary that renders
        # "wave 1: 3/3 done" — not a full list of its members.
        assert wave1 == {
            "id": 1, "total": 3, "status_counts": {"completed": 3},
        }
        assert "tasks" not in wave1

        # The running frontier keeps its detail: started tasks, live job.
        assert wave2["id"] == 2
        assert wave2["status_counts"] == {
            "completed": 1, "in_progress": 1, "pending": 1,
        }
        started = {t["id"]: t for t in wave2["tasks"]}
        assert started["2.2"]["status"] == "in_progress"
        assert started["2.2"]["jobs"] == [{
            "id": "j_live_22", "status": "running",
            "started_at": datetime.fromtimestamp(
                1787000200.0, timezone.utc,
            ).isoformat(),
        }]
        # The pending member is not enumerated: its count is in the wave.
        assert "2.3" not in started

        # Terminal jobs inside the incomplete plan are still reported — as
        # the plan's tally, not as an enumeration that grows with history.
        assert plan["job_status_counts"] == {"completed": 8}
        for done in ("j_done_1.1_0", "j_done_2.1_1"):
            assert done not in json.dumps(body)
        # Nothing claimed by the structure rides the top level twice.
        assert body["tasks"] == []
        assert body["jobs"] == []

    def test_payload_does_not_grow_with_completed_history(self, tmp_path):
        """The twelve-day session pinned: more settled jobs, same payload.

        The plan's shape is identical in both snapshots; only the settled
        job history differs. The payload must not re-ship that history, so
        its size is flat and its frontier detail is byte-identical.
        """
        small = _snapshot(
            _shaped_project(tmp_path / "a", attempts_per_completed_task=3))
        big = _snapshot(
            _shaped_project(tmp_path / "b", attempts_per_completed_task=40))
        small["snapshot_at"] = big["snapshot_at"] = "fixed"
        small_blob, big_blob = json.dumps(small), json.dumps(big)
        # Eight times the settled job history moves the payload by the digits
        # of its count — not by anything proportional to the history itself.
        assert len(big_blob) - len(small_blob) < 2
        # The frontier detail is identical, and the history's members are
        # never enumerated.
        def frontier(b):
            return json.dumps(b["plans"][0]["waves"][1])
        assert frontier(small) == frontier(big)
        assert "j_done_1.2_9" not in big_blob
        assert big["plans"][0]["job_status_counts"] == {"completed": 160}

    def test_settled_plan_is_a_count_node(self, tmp_path):
        """A finished plan keeps its summary row but loses its member lists."""
        root = tmp_path / "oldplans"
        _make_plan(root, "done_plan", _WAVES_YML, {
            "1.1": "completed", "1.2": "completed", "1.3": "completed",
            "2.1": "completed", "2.2": "completed", "2.3": "completed",
        })
        for i in range(60):
            _make_job(root, f"j_old_{i}", "completed", "1.1", 1787000000.0 + i)
        _make_plan(root, "live_plan", _WAVES_YML, {
            "1.1": "in_progress",
        })
        body = _snapshot(root, "sess_old")
        done, live = body["plans"]
        assert done["name"] == "done_plan"
        assert done["status_counts"] == {"completed": 6}
        assert "waves" not in done and "tasks" not in done
        assert done["job_status_counts"] == {"completed": 60}
        assert live["waves"][0]["tasks"][0]["id"] == "1.1"

    def test_enumerated_task_carries_its_registry_wave_id(self, tmp_path):
        root = tmp_path / "enumerated"
        _make_plan(root, "plan", "waves:\n  - id: 1\n    tasks: ['1.1']\n", {
            "1.1": "in_progress",
        })
        _make_task(root, "1.1", "in_progress", "w_0009")

        body = _snapshot(root)

        task = body["plans"][0]["waves"][0]["tasks"][0]
        assert task["id"] == "1.1"
        assert task["status"] == "in_progress"
        assert task["wave_id"] == "w_0009"

    def test_settled_wave_carries_ids_without_enumerating_tasks(self, tmp_path):
        root = tmp_path / "settled"
        _make_plan(root, "plan", "waves:\n  - id: 1\n    tasks: ['1.1', '1.2']\n  - id: 2\n    tasks: ['2.1']\n", {
            "1.1": "completed", "1.2": "completed", "2.1": "in_progress",
        })
        _make_task(root, "1.1", "completed", "w_0009")
        _make_task(root, "1.2", "completed", "w_0009")
        _make_task(root, "2.1", "in_progress")

        wave = _snapshot(root)["plans"][0]["waves"][0]

        assert wave["wave_ids"] == ["w_0009"]
        assert "tasks" not in wave

    def test_settled_wave_reports_distinct_registry_ids(self, tmp_path):
        root = tmp_path / "mixed"
        _make_plan(root, "plan", "waves:\n  - id: 1\n    tasks: ['1.1', '1.2', '1.3']\n  - id: 2\n    tasks: ['2.1']\n", {
            "1.1": "completed", "1.2": "completed", "1.3": "completed", "2.1": "in_progress",
        })
        _make_task(root, "1.1", "completed", "w_0009")
        _make_task(root, "1.2", "completed", "w_000a")
        _make_task(root, "1.3", "completed", "w_0009")
        _make_task(root, "2.1", "in_progress")

        wave = _snapshot(root)["plans"][0]["waves"][0]

        assert wave["wave_ids"] == ["w_0009", "w_000a"]
        assert "tasks" not in wave

    def test_unclassified_tasks_do_not_invent_registry_ids(self, tmp_path):
        root = tmp_path / "unclassified"
        _make_plan(root, "plan", "waves:\n  - id: 1\n    tasks: ['1.1']\n  - id: 2\n    tasks: ['2.1']\n", {
            "1.1": "completed", "2.1": "in_progress",
        })
        _make_task(root, "1.1", "completed")
        _make_task(root, "2.1", "in_progress")

        wave = _snapshot(root)["plans"][0]["waves"][0]

        assert "wave_ids" not in wave
        assert "wave_id" not in json.dumps(_snapshot(root))

    def test_a_record_the_plan_collapsed_away_still_surfaces_while_live(
        self, tmp_path,
    ):
        """Collapsing reshapes the picture; it never hides what is running."""
        root = _shaped_project(tmp_path, attempts_per_completed_task=1)
        _make_plan(root, "settled_plan", _WAVES_YML, {
            "1.1": "completed",
        })
        # A zombie engine record: the plan says completed, the state says
        # running. The plan node collapsed its task away, so the live record
        # must surface at the top level rather than vanish.
        _write(root / ".snodo" / "tasks" / "1.1" / "state.json", {
            "status": "running", "started_at": 1787000000.0,
        })
        body = _snapshot(root)
        assert [t["id"] for t in body["tasks"]] == ["1.1"]


# ------------------------------------------------------------------ #
# Liveness URL derivation & rejection visibility (Fixes #293)
# ------------------------------------------------------------------ #


class TestLivenessUrlDerivation:
    def test_default_config_composes_app_origin_and_version(self):
        """Default configuration PUTs to app origin with /v1 segment."""
        posts = _Posts()
        default_config = {
            "cloud": {
                "sync_enabled": True,
                "api_key": "sndo_live_testkey",
            },
        }
        with patch("httpx.put", posts):
            cloud_liveness._post_snapshot({"session_id": "sess_default_1"}, config=default_config)
        assert len(posts.calls) == 1
        url, _ = posts.calls[0]
        assert url == "https://app.snodo.dev/v1/live/sess_default_1/ls_test_lease"

    def test_non_production_shape_composes_usable_url(self):
        """Localhost or unlabelled hosts retain origin and append /v1."""
        posts = _Posts()
        local_config = {
            "cloud": {
                "sync_enabled": True,
                "api_key": "sndo_live_testkey",
                "api_url": "http://localhost:9000",
            },
        }
        with patch("httpx.put", posts):
            cloud_liveness._post_snapshot({"session_id": "sess_local_1"}, config=local_config)
        assert len(posts.calls) == 1
        url, _ = posts.calls[0]
        assert url == "http://localhost:9000/v1/live/sess_local_1/ls_test_lease"


class TestTerminalRefusal:
    def test_authentication_failure_stops_later_liveness_pushes(self, tmp_path):
        """A terminal 401 is persisted and prevents a later PUT."""
        from snodo.infrastructure.cloud_sync import CloudSyncState

        snap = {"session_id": "sess_auth_1"}
        refused = type("R", (), {"status_code": 401, "text": "revoked"})()

        with patch.object(cloud_liveness, "resolve_home", lambda: tmp_path), \
                patch("snodo.infrastructure.cloud_sync.resolve_home", lambda: tmp_path):
            with patch("httpx.put", return_value=refused) as put:
                cloud_liveness._post_snapshot(snap)
                cloud_liveness._post_snapshot(snap)

            assert put.call_count == 1
            assert CloudSyncState(tmp_path / "cloud_sync.json").is_refused("sess_auth_1")

    def test_terminal_refusal_survives_restart(self, tmp_path):
        """Resetting in-memory liveness bookkeeping does not clear refusal state."""
        from snodo.infrastructure.cloud_sync import CloudSyncState

        snap = {"session_id": "sess_auth_restart"}
        refused = type("R", (), {"status_code": 403, "text": "invalid key"})()

        with patch.object(cloud_liveness, "resolve_home", lambda: tmp_path), \
                patch("snodo.infrastructure.cloud_sync.resolve_home", lambda: tmp_path):
            with patch("httpx.put", return_value=refused):
                cloud_liveness._post_snapshot(snap)
            cloud_liveness.reset_liveness_state()
            with patch("httpx.put") as put:
                cloud_liveness._post_snapshot(snap)

            put.assert_not_called()
            assert CloudSyncState(tmp_path / "cloud_sync.json").is_refused(
                "sess_auth_restart",
            )

    def test_rate_limit_does_not_stop_later_liveness_pushes(self, tmp_path):
        """A 429 is retryable and does not create a persisted refusal."""
        from snodo.infrastructure.cloud_sync import CloudSyncState

        snap = {"session_id": "sess_rate_limit"}
        limited = type("R", (), {"status_code": 429, "text": "slow down"})()
        accepted = type("R", (), {"status_code": 204, "text": ""})()

        with patch.object(cloud_liveness, "resolve_home", lambda: tmp_path), \
                patch("snodo.infrastructure.cloud_sync.resolve_home", lambda: tmp_path):
            with patch("httpx.put", side_effect=[limited, accepted]) as put:
                cloud_liveness._post_snapshot(snap)
                cloud_liveness._post_snapshot(snap)

            assert put.call_count == 2
            assert not CloudSyncState(tmp_path / "cloud_sync.json").is_refused(
                "sess_rate_limit",
            )


# ------------------------------------------------------------------ #
# Two clocks: last_event (decisions) vs last_activity_at (happenings)
# ------------------------------------------------------------------ #


def _age(path: Path, seconds: float = 5 * 3600.0) -> None:
    """Set a file's mtime seconds into the past (a five-hour-old write)."""
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _iso(body_value: str) -> datetime:
    return datetime.fromisoformat(body_value)


class TestLastActivityClock:
    """A status write moves ``last_activity_at`` without moving ``last_event``;
    an audit event moves both. ``last_event`` keeps its exact meaning: the
    last recorded decision, never relabelled (Fixes #324)."""

    def test_plan_status_write_moves_last_activity_not_last_event(self, project):
        """The reported bug: a plan advanced to done while the audit tail
        stayed five hours behind. ``last_event`` must not change meaning."""
        root, _ = project
        old_decision = "2026-09-02T21:50:01+00:00"
        (root / ".snodo" / "audit.log").write_text(json.dumps({
            "sequence": 3, "timestamp": old_decision,
            "event_type": "session_decision_updated",
            "data": {"key": "scope", "value": "full"},
        }) + "\n")
        # The last write happened hours ago, as the audit tail claims.
        _age(root / ".snodo" / "plans" / "wave8" / "status.json")
        _age(root / ".snodo" / "tasks" / "t_alpha" / "state.json")
        before = cloud_liveness.build_liveness_snapshot("sess_test_1", str(root))
        assert before["last_event"] == {
            "event_type": "session_decision_updated", "timestamp": old_decision,
        }
        assert _iso(before["last_activity_at"]).timestamp() < time.time() - 3600

        # The planner writes the plan's last task complete: real activity,
        # no audit event appended.
        posts = _Posts()
        from snodo.mcp.planner import PlannerMCP
        planner = PlannerMCP(str(root))
        with patch("httpx.put", posts):
            planner.update_status("wave8", "2.1", "completed")
            cloud_liveness.wait_for_pushes()

        assert posts.calls, "the status write should still fire its push"
        _, body = posts.calls[0]
        # The new clock moved with the write; the old one stayed pinned.
        assert _iso(body["last_activity_at"]) > _iso(before["last_activity_at"])
        assert _iso(body["last_activity_at"]).timestamp() > time.time() - 120
        assert body["last_event"] == before["last_event"]

    def test_task_status_write_moves_the_new_timestamp(self, project):
        """An engine task record rewritten in place moves last_activity_at —
        file-level write, no planner, no audit event, same promise."""
        root, _ = project
        _age(root / ".snodo" / "plans" / "wave8" / "status.json")
        _age(root / ".snodo" / "tasks" / "t_alpha" / "state.json")
        before = cloud_liveness.build_liveness_snapshot("sess_test_1", str(root))
        _write(root / ".snodo" / "tasks" / "t_alpha" / "state.json", {
            "task_id": "t_alpha", "status": "completed",
            "started_at": 1787000000.0,
        })
        after = cloud_liveness.build_liveness_snapshot("sess_test_1", str(root))
        assert _iso(after["last_activity_at"]) > _iso(before["last_activity_at"])
        assert after["last_event"] == before["last_event"]  # no audit log here

    def test_audit_event_moves_both_clocks(self, project):
        """A recorded decision is also something that happened: it moves
        last_event and, through its own timestamp, last_activity_at — even
        when every record file is older."""
        root, _ = project
        _age(root / ".snodo" / "plans" / "wave8" / "status.json")
        _age(root / ".snodo" / "tasks" / "t_alpha" / "state.json")
        before = cloud_liveness.build_liveness_snapshot("sess_test_1", str(root))
        from snodo.infrastructure.audit import AuditLog
        log = AuditLog(str(root / ".snodo" / "audit.log"),
                       project_id="local:test123")
        log.append_event("dispatch", {"task_ref": "2.1",
                                      "session_id": "sess_test_1"})
        after = cloud_liveness.build_liveness_snapshot("sess_test_1", str(root))
        assert after["last_event"]["event_type"] == "dispatch"
        assert _iso(after["last_activity_at"]) > _iso(before["last_activity_at"])
        # The event is the freshest thing that happened: the clocks agree on it.
        assert _iso(after["last_activity_at"]) == _iso(
            after["last_event"]["timestamp"],
        )

    def test_last_activity_is_the_newest_evidence_across_sections(self, project):
        """One old plan, one fresh job: the clock reads the newest file the
        snapshot reports, not the audit tail alone."""
        root, _ = project
        _age(root / ".snodo" / "plans" / "wave8" / "status.json")
        _age(root / ".snodo" / "tasks" / "t_alpha" / "state.json")
        plan_stamp = time.time() - 4 * 3600
        os.utime(root / ".snodo" / "plans" / "wave8" / "status.json",
                 (plan_stamp, plan_stamp))
        _write(root / ".snodo" / "jobs" / "j_20260902_a9" / "state.json", {
            "status": "running", "started_at": 1787000001.0,
        })
        body = cloud_liveness.build_liveness_snapshot("sess_test_1", str(root))
        assert _iso(body["last_activity_at"]).timestamp() > time.time() - 120
        assert _iso(body["last_activity_at"]) > _iso(
            datetime.fromtimestamp(plan_stamp, timezone.utc).isoformat(),
        )
