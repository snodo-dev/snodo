"""Best-effort operator notifications for background jobs.

The monitor runs detached from the job process so network delivery can never
hold up execution. A filesystem claim makes delivery idempotent across the
plan, task, and monitor processes spawned for one run.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from snodo.config import ConfigError, ConfigManager

logger = logging.getLogger(__name__)
EVENTS = {"job_finished", "task_halted", "authorization_needed", "job_silent"}
_DELIVERY_TIMEOUT = 2
_POLL_SECONDS = 0.1
_MISSING_REFERENCES: set[str] = set()


def _settings() -> dict:
    value = ConfigManager().load().get("notifications", {})
    return value if isinstance(value, dict) else {}


def _targets(settings: dict) -> list[dict]:
    result = []
    for target in settings.get("targets", []):
        if not isinstance(target, dict) or target.get("type") not in {"webhook", "ntfy", "slack", "discord", "teams"}:
            continue
        resolved = dict(target)
        invalid_reference = False
        for key in ("url", "token"):
            value = resolved.get(key)
            if isinstance(value, str) and ":" in value:
                scheme, _, _ = value.partition(":")
                if scheme in {"env", "command"}:
                    try:
                        resolved[key] = ConfigManager._resolve_key_reference(value)
                    except ConfigError:
                        if value not in _MISSING_REFERENCES:
                            logger.warning("Unable to resolve notification %s reference", key)
                            _MISSING_REFERENCES.add(value)
                        invalid_reference = True
        url = resolved.get("url")
        if not invalid_reference and isinstance(url, str) and url.startswith(("https://", "http://")):
            result.append(resolved)
    return result


def redact_notifications(config: dict) -> dict:
    """Return a copy with all target endpoints and credentials hidden."""
    import copy

    clean = copy.deepcopy(config)
    section = clean.get("notifications")
    if isinstance(section, dict):
        for target in section.get("targets", []):
            if isinstance(target, dict):
                for key in ("url", "token", "authorization", "headers"):
                    if target.get(key):
                        target[key] = "[redacted]"
    return clean


def _display_name(root: Path) -> str | None:
    """Read a project's configured name, including from a linked worktree's main checkout."""
    roots = [root]
    try:
        import subprocess

        result = subprocess.run(  # noqa: S603 - fixed argv, project path passed as one argument
            ["git",  # noqa: S607 - git resolved from PATH by design
             "-C", str(root), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            main_root = Path(result.stdout.strip()).resolve().parent
            if main_root not in roots:
                roots.append(main_root)
    except Exception:  # noqa: BLE001,S110 - identity lookup must never prevent notification
        pass

    for project_root in roots:
        try:
            cached_project = json.loads((project_root / ".snodo" / "project.json").read_text())
            value = cached_project.get("display_name")
            if isinstance(value, str) and value.strip():
                return value.strip()
        except (OSError, ValueError, AttributeError):
            continue
    return None


def send(target: dict, event: dict) -> None:
    """Deliver one short event with a hard network timeout."""
    kind = target["type"]
    message = event["message"]
    if kind == "ntfy":
        body = message.encode("utf-8")
        project = event.get("project")
        host = event.get("host")
        title = " - ".join(value for value in (project, host) if value) or "Snodo job update"
        headers = {"Content-Type": "text/plain; charset=utf-8", "Title": title}
    elif kind == "slack":
        first_line, separator, remainder = message.partition("\n")
        if event.get("event") == "job_finished" and event.get("status") in {"completed", "failed"}:
            status = event.get("status")
            exit_code = event.get("exit_code")
            icon = ":white_check_mark:" if status == "completed" and exit_code == 0 else ":warning:"
            first_line = f"{icon} {first_line}"
        # Slack mrkdwn uses single asterisks for bold; the shared message uses
        # Markdown's double-asterisk syntax for Discord and generic consumers.
        bold_start = first_line.find("**")
        if bold_start >= 0 and (bold_end := first_line.find("**", bold_start + 2)) >= 0:
            first_line = (
                first_line[:bold_start]
                + f"*{first_line[bold_start + 2:bold_end]}*"
                + first_line[bold_end + 2:]
            )
        slack_message = first_line + (separator + remainder if separator else "")
        body = json.dumps({"text": slack_message}, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    elif kind == "discord":
        body = json.dumps({"content": message, "allowed_mentions": {"parse": []}}, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    elif kind == "teams":
        card = {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "type": "AdaptiveCard",
            "version": "1.2",
            "body": ([
                {"type": "TextBlock", "text": event["project"], "weight": "Bolder", "size": "Medium", "wrap": True},
                {"type": "TextBlock", "text": f"Runner: {event['host']}", "isSubtle": True, "wrap": True},
                {"type": "TextBlock", "text": message.partition("\n")[2] or message, "wrap": True},
            ] if event.get("project") and event.get("host") else [
                {"type": "TextBlock", "text": message, "wrap": True},
            ]),
        }
        body = json.dumps({
            "type": "message",
            "attachments": [{
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": card,
            }],
        }, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    else:
        body = json.dumps(event, separators=(",", ":")).encode("utf-8")
        headers = {"Content-Type": "application/json"}
    token = target.get("token")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    headers.update(target.get("headers", {}) if kind == "webhook" and isinstance(target.get("headers"), dict) else {})
    request = Request(target["url"], data=body, headers=headers, method="POST")
    with urlopen(request, timeout=_DELIVERY_TIMEOUT) as response:  # noqa: S310 - operator-configured notification target
        if response.status >= 400:
            raise URLError(f"HTTP {response.status}")


def test_targets() -> list[tuple[str, bool]]:
    """Send one test message to every valid configured target."""
    targets = _targets(_settings())
    if not targets:
        return []
    event = {"event": "test", "message": "Snodo notification test — notifications are configured."}
    results = []
    for index, target in enumerate(targets, 1):
        try:
            send(target, event)
        except Exception:  # delivery errors can contain endpoint/token details
            logger.warning("Notification delivery failed for configured target %d", index)
            results.append((target.get("name") or f"{target['type']} {index}", False))
        else:
            results.append((target.get("name") or f"{target['type']} {index}", True))
    return results


def _claim(sent_dir: Path, event_id: str) -> bool:
    sent_dir.mkdir(parents=True, exist_ok=True)
    marker = sent_dir / hashlib.sha256(event_id.encode()).hexdigest()
    try:
        fd = os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def _notify(root: Path, event_id: str, event_type: str, message: str, details: dict) -> None:
    settings = _settings()
    if event_type not in settings.get("events", sorted(EVENTS)):
        return
    targets = _targets(settings)
    if not targets or not _claim(root / ".snodo" / "notifications" / "sent", event_id):
        return
    payload = {"event": event_type, "message": message, **details}
    for index, target in enumerate(targets, 1):
        try:
            send(target, payload)
        except Exception:  # endpoint errors may include credentials
            logger.warning("Notification delivery failed for configured target %d", index)


def _job_message(root: Path, job_id: str, task: dict, happened: str) -> tuple[str, dict]:
    display_name = _display_name(root)
    try:
        from snodo.project import get_project_id

        project_id, _scope = get_project_id(str(root))
    except Exception:  # identity lookup must never prevent a best-effort notification
        project_id = None
    project = display_name or project_id or root.name
    host = socket.gethostname()
    plan = task.get("plan_name") or task.get("task_plan")
    task_id = task.get("task_id")
    details = [f"job {job_id}"]
    if plan:
        details.append(f"plan {plan}")
    if task_id:
        details.append(f"task {task_id}")
    details.extend([happened, f"Inspect: snodo logs {job_id}"])
    message = f"**{project}** · {host}\n" + " — ".join(details)
    return message, {
        "project": project, "host": host, "job_id": job_id,
        "plan": plan, "task": task_id, "command": f"snodo logs {job_id}",
    }


def monitor(root_text: str, job_id: str) -> None:
    root = Path(root_text)
    job_dir = root / ".snodo" / "jobs" / job_id
    try:
        task = json.loads((job_dir / "task.json").read_text())
        state = json.loads((job_dir / "state.json").read_text())
    except (OSError, ValueError):
        return
    started = float(state.get("started_at") or state.get("created_at") or time.time())
    task_id = task.get("task_id")
    audit_path = root / ".snodo" / "audit.log"
    audit_offset = 0
    last_activity = started
    silent_sent = False
    threshold_value = _settings().get("silence_threshold_seconds", 900)
    try:
        threshold = max(1, int(threshold_value))
    except (TypeError, ValueError):
        threshold = 900
    while True:
        try:
            with (job_dir / "state.json").open() as stream:
                state = json.load(stream)
        except (OSError, ValueError):
            return

        # Route existing audit events; this adds no audit vocabulary or state.
        try:
            with audit_path.open("rb") as stream:
                stream.seek(audit_offset)
                while line := stream.readline():
                    audit_offset = stream.tell()
                    try:
                        item = json.loads(line)
                        event_time = item.get("timestamp")
                        if event_time and event_time < time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(started)):
                            continue
                        data = item
                        # Plan/queue monitors must not re-notify the child
                        # task's audit event; only the owning task job matches.
                        same_task = bool(task_id) and data.get("task_ref") == task_id
                        if not same_task:
                            continue
                        event_type = item.get("event_type")
                        if event_type == "halt":
                            halt_type = data.get("raw_halt_type") or data.get("halt_type") or "halted"
                            message, details = _job_message(root, job_id, task, f"task halted ({halt_type})")
                            _notify(root, f"halt:{item.get('event_hash', audit_offset)}", "task_halted", message, details)
                        elif event_type == "disagreement_escalated":
                            message, details = _job_message(root, job_id, task, "needs human decision; next: snodo authorize")
                            details["command"] = "snodo authorize"
                            _notify(root, f"authorize:{item.get('event_hash', audit_offset)}", "authorization_needed", message, details)
                    except (TypeError, ValueError):
                        continue
        except OSError:
            pass

        if state.get("status") in {"completed", "failed"}:
            message, details = _job_message(root, job_id, task, f"job {state['status']}")
            details.update(status=state["status"], exit_code=state.get("exit_code"))
            _notify(root, f"{job_id}:finished:{state['status']}", "job_finished", message, details)
            return
        if state.get("status") in {"cancelled", "unmerged"}:
            return

        activity = max((job_dir / name).stat().st_mtime for name in ("stdout.log", "stderr.log") if (job_dir / name).exists()) if any((job_dir / name).exists() for name in ("stdout.log", "stderr.log")) else started
        last_activity = max(last_activity, activity)
        if not silent_sent and time.time() - last_activity >= threshold:
            message, details = _job_message(root, job_id, task, f"no log activity for {threshold}s")
            _notify(root, f"{job_id}:silent:{int(last_activity)}", "job_silent", message, details)
            silent_sent = True
        time.sleep(_POLL_SECONDS)


def main() -> None:
    if len(sys.argv) == 4 and sys.argv[1] == "monitor":
        monitor(sys.argv[2], sys.argv[3])


if __name__ == "__main__":
    main()
