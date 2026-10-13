"""Read-only local audit progress helpers for ``snodo cloud status``."""

from pathlib import Path

UNSENT_NOTICE_THRESHOLD = 10


def local_sync_issues() -> list[tuple[str, int, bool]]:
    """Return locally known refused or substantially backlogged sessions.

    Each entry is ``(session_id, unsent_count, refused)``. This is a local-only
    check and intentionally avoids credential resolution and network access.
    """
    from snodo.config import ConfigManager
    from snodo.infrastructure.cloud_sync import CloudSyncState

    config = ConfigManager().load()
    cloud = config.get("cloud", {}) if isinstance(config, dict) else {}
    if not cloud.get("sync_enabled", False):
        return []

    state = CloudSyncState()
    summary = state.get_summary()
    counts = local_unsent_counts(summary)
    issues = []
    for session_id, info in summary.items():
        if not session_id or session_id.startswith("project:"):
            continue
        unsent = counts.get(session_id, (0, 0))[0]
        refused = state.is_refused(session_id)
        if refused or unsent > UNSENT_NOTICE_THRESHOLD:
            issues.append((session_id, unsent, refused))
    return issues


def sync_issue_notice() -> str | None:
    """Format the operator-facing notice for local sync issues."""
    issues = local_sync_issues()
    if not issues:
        return None
    sessions = ", ".join(
        f"{session_id} ({count} unsent{', refused' if refused else ''})"
        for session_id, count, refused in issues
    )
    return f"Cloud sync needs attention: {sessions}. Run `snodo cloud sync --all --force`."


def local_unsent_counts(summary: dict) -> dict[str, tuple[int, int]]:
    """Return ``key -> (unsent event count, local chain head)`` for this project.

    An empty mapping means there is no reachable project audit chain. Session
    cursors are compared only with events carrying that session id; the
    cursors cover a prefix of the project's single audit chain, so each count
    includes every event above that cursor, regardless of session attribution.
    """
    from snodo.paths import resolve_project_root

    root = resolve_project_root()
    if not root:
        return {}
    audit_path = Path(root) / ".snodo" / "audit.log"
    if not audit_path.is_file():
        return {}

    from snodo.infrastructure.audit import AuditLog
    from snodo.infrastructure.cloud_sync import CloudSyncState

    try:
        events = AuditLog(str(audit_path)).events
    except Exception:
        return {}
    project_ids = {getattr(event, "project_id", "") for event in events}
    project_ids.discard("")
    if len(project_ids) != 1:
        return {}
    project_id = next(iter(project_ids))
    state = CloudSyncState()
    head = max((event.sequence for event in events), default=-1)
    result: dict[str, tuple[int, int]] = {}

    project_key = state.project_cursor_id(project_id)
    if project_key in summary:
        cursor = state.get_project_cursor(project_id)
        count = sum(event.sequence > cursor for event in events)
        result[project_key] = (count, head)

    for key in summary:
        if not key or key.startswith("project:"):
            continue
        if not any(getattr(event, "data", {}).get("session_id") == key for event in events):
            continue
        cursor = max(state.get_cursor(key), state.get_project_cursor(project_id))
        result[key] = (sum(event.sequence > cursor for event in events), head)
    return result
