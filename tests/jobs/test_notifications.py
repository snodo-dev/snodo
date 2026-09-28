"""Notification delivery and monitor behavior."""

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from snodo.jobs import notifications


class StubHandler(BaseHTTPRequestHandler):
    requests = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.requests.append((self.path, self.headers, body))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *_args):
        pass


def server():
    StubHandler.requests = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def test_webhook_json_and_ntfy_plain_posts():
    httpd = server()
    try:
        base = f"http://127.0.0.1:{httpd.server_port}"
        event = {"event": "test", "message": "job finished"}
        notifications.send({"type": "webhook", "url": base + "/hook"}, event)
        notifications.send({"type": "ntfy", "url": base + "/topic"}, event)
        assert StubHandler.requests[0][0] == "/hook"
        assert json.loads(StubHandler.requests[0][2]) == event
        assert StubHandler.requests[1][0] == "/topic"
        assert StubHandler.requests[1][2] == b"job finished"
    finally:
        httpd.shutdown()


def test_chat_platform_payloads_render_the_actionable_message():
    httpd = server()
    try:
        base = f"http://127.0.0.1:{httpd.server_port}"
        event = {
            "event": "job_finished",
            "message": "project: job j_123 — plan nightly — task t_456 — job completed — Inspect: snodo logs j_123",
            "project": "project",
            "job_id": "j_123",
            "plan": "nightly",
            "task": "t_456",
            "command": "snodo logs j_123",
        }
        for kind in ("slack", "discord", "teams"):
            notifications.send({"type": kind, "url": f"{base}/{kind}"}, event)

        requests = {path.removeprefix("/"): json.loads(body) for path, _headers, body in StubHandler.requests}
        assert requests["slack"] == {"text": event["message"]}
        assert requests["discord"] == {"content": event["message"], "allowed_mentions": {"parse": []}}
        teams = requests["teams"]
        assert teams["type"] == "message"
        attachment = teams["attachments"][0]
        assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
        assert attachment["contentUrl"] is None
        assert attachment["content"]["type"] == "AdaptiveCard"
        assert attachment["content"]["version"] == "1.2"
        assert attachment["content"]["body"] == [{"type": "TextBlock", "text": event["message"], "wrap": True}]
    finally:
        httpd.shutdown()


def test_notify_test_sends_to_every_configured_target_type(monkeypatch):
    httpd = server()
    try:
        base = f"http://127.0.0.1:{httpd.server_port}"
        kinds = ("webhook", "ntfy", "slack", "discord", "teams")
        monkeypatch.setattr(notifications, "_settings", lambda: {
            "targets": [{"type": kind, "name": kind, "url": f"{base}/{kind}"} for kind in kinds],
        })

        results = notifications.test_targets()

        assert results == [(kind, True) for kind in kinds]
        assert [request[0] for request in StubHandler.requests] == [f"/{kind}" for kind in kinds]
        bodies = [json.loads(body) if path != "/ntfy" else body.decode() for path, _headers, body in StubHandler.requests]
        assert bodies[0]["event"] == "test"
        assert bodies[1] == "Snodo notification test — notifications are configured."
        assert bodies[2] == {"text": "Snodo notification test — notifications are configured."}
        assert bodies[3]["content"] == "Snodo notification test — notifications are configured."
        assert bodies[4]["attachments"][0]["content"]["type"] == "AdaptiveCard"
    finally:
        httpd.shutdown()


def test_notification_url_and_token_resolve_environment_references(monkeypatch):
    monkeypatch.setenv("SNODO_TEST_HOOK", "http://127.0.0.1/hook")
    monkeypatch.setenv("SNODO_TEST_TOKEN", "secret-token")
    targets = notifications._targets({"targets": [{
        "type": "slack", "url": "env:SNODO_TEST_HOOK", "token": "env:SNODO_TEST_TOKEN",
    }]})
    assert targets == [{"type": "slack", "url": "http://127.0.0.1/hook", "token": "secret-token"}]


def test_missing_environment_reference_warns_once_and_does_not_affect_job(tmp_path, monkeypatch, caplog):
    root = tmp_path
    job = root / ".snodo" / "jobs" / "j_missing-reference"
    job.mkdir(parents=True)
    state = {"status": "completed", "exit_code": 0, "started_at": 1}
    (job / "task.json").write_text(json.dumps({"description": "Completed work"}))
    (job / "state.json").write_text(json.dumps(state))
    monkeypatch.delenv("SNODO_MISSING_HOOK", raising=False)
    monkeypatch.setattr(notifications, "_settings", lambda: {
        "targets": [{"type": "slack", "url": "env:SNODO_MISSING_HOOK"}],
        "events": ["job_finished"],
    })

    notifications.monitor(str(root), job.name)
    notifications.monitor(str(root), job.name)

    assert json.loads((job / "state.json").read_text()) == state
    assert sum("Unable to resolve notification url reference" in record.message for record in caplog.records) == 1


def test_filtering_deduplication_and_delivery_failure_are_best_effort(tmp_path, monkeypatch):
    httpd = server()
    try:
        root = tmp_path
        monkeypatch.setattr(notifications, "_settings", lambda: {
            "targets": [{"type": "webhook", "url": f"http://127.0.0.1:{httpd.server_port}/hook"}],
            "events": ["job_finished"],
        })
        notifications._notify(root, "ignored", "task_halted", "halt", {})
        assert StubHandler.requests == []
        notifications._notify(root, "one", "job_finished", "finished", {})
        notifications._notify(root, "one", "job_finished", "finished", {})
        assert len(StubHandler.requests) == 1

        monkeypatch.setattr(notifications, "_settings", lambda: {
            "targets": [{"type": "webhook", "url": "http://127.0.0.1:1/unreachable"}],
            "events": ["job_finished"],
        })
        # A target failure is swallowed and still claimed once.
        notifications._notify(root, "failure", "job_finished", "finished", {})
        notifications._notify(root, "failure", "job_finished", "finished", {})
    finally:
        httpd.shutdown()


def test_silence_threshold_fires_once_and_ends_on_completion(tmp_path, monkeypatch):
    root = tmp_path
    job_id = "j_test"
    job = root / ".snodo" / "jobs" / job_id
    job.mkdir(parents=True)
    (job / "task.json").write_text(json.dumps({"task_id": "t_test", "description": "Build feature"}))
    (job / "state.json").write_text(json.dumps({"status": "running", "started_at": 1, "created_at": 1}))
    (job / "stdout.log").write_text("")
    os.utime(job / "stdout.log", (1, 1))
    now = [10]
    sent = []
    monkeypatch.setattr(notifications, "_settings", lambda: {"events": ["job_silent"], "silence_threshold_seconds": 5})
    monkeypatch.setattr(notifications, "_notify", lambda *args: sent.append(args))
    monkeypatch.setattr(notifications.time, "time", lambda: now[0])

    def advance(_seconds):
        now[0] += 5
        (job / "state.json").write_text(json.dumps({"status": "completed", "started_at": 1, "created_at": 1}))

    monkeypatch.setattr(notifications.time, "sleep", advance)
    notifications.monitor(str(root), job_id)
    silence_events = [entry for entry in sent if entry[2] == "job_silent"]
    assert len(silence_events) == 1


def test_config_redaction_hides_target_url_and_token():
    config = {"notifications": {"targets": [
        {"type": kind, "url": "https://secret.invalid/hook", "token": "top-secret"}
        for kind in ("webhook", "ntfy", "slack", "discord", "teams")
    ]}}
    redacted = notifications.redact_notifications(config)
    assert redacted["notifications"]["targets"][0]["url"] == "[redacted]"
    assert redacted["notifications"]["targets"][0]["token"] == "[redacted]"
    assert "secret.invalid" not in json.dumps(redacted)
    assert config["notifications"]["targets"][0]["token"] == "top-secret"


def test_config_show_redacts_notification_credentials(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from snodo.cli.main import app

    monkeypatch.setenv("SNODO_HOME", str(tmp_path))
    (tmp_path / "config.yml").write_text(
        "notifications:\n"
        "  targets:\n"
        "    - type: webhook\n"
        "      name: private-hook\n"
        "      url: https://secret.invalid/path?token=URLSECRET\n"
        "      token: TOKENSECRET\n"
    )
    result = CliRunner().invoke(app, ["config", "show"])
    assert result.exit_code == 0, result.output
    assert "private-hook" in result.output
    assert "secret.invalid" not in result.output
    assert "URLSECRET" not in result.output
    assert "TOKENSECRET" not in result.output


def test_notification_failure_does_not_change_terminal_job(tmp_path, monkeypatch):
    root = tmp_path
    job = root / ".snodo" / "jobs" / "j_failed-delivery"
    job.mkdir(parents=True)
    (job / "task.json").write_text(json.dumps({"description": "Completed work"}))
    state = {"status": "completed", "exit_code": 0, "started_at": 1}
    (job / "state.json").write_text(json.dumps(state))
    monkeypatch.setattr(notifications, "_settings", lambda: {
        "targets": [{"type": "ntfy", "url": "http://127.0.0.1:1/topic"}],
        "events": ["job_finished"],
    })
    notifications.monitor(str(root), job.name)
    assert json.loads((job / "state.json").read_text()) == state
