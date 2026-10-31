"""Immutable local intent records (ADR 026/027/038)."""
from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime
from pathlib import Path

from snodo.infrastructure.atomic_json import atomic_write_json


class IntentError(ValueError):
    """Invalid intent operation or record."""


_ID_RE = re.compile(r"^int_[0-9a-f]{6}$")
MAX_INTENT_TEXT_LENGTH = 10_000


class IntentStore:
    """Store immutable intent records in a project's .snodo directory."""

    def __init__(self, project_root: str | Path, audit_log=None):
        self.project_root = Path(project_root).resolve()
        self.directory = self.project_root / ".snodo" / "intents"
        self._audit_log = audit_log

    def record(self, text: str, issue: str | None = None, supersedes: str | None = None) -> dict:
        if not isinstance(text, str) or not text.strip():
            raise IntentError("Intent text must be non-empty")
        if len(text) > MAX_INTENT_TEXT_LENGTH:
            raise IntentError(f"Intent text exceeds {MAX_INTENT_TEXT_LENGTH} characters")
        if supersedes is not None:
            self.get(supersedes)
        self.directory.mkdir(parents=True, exist_ok=True)
        while True:
            intent_id = f"int_{secrets.token_hex(3)}"
            record_dir = self.directory / intent_id
            try:
                record_dir.mkdir()
                break
            except FileExistsError:
                continue
        record = {"id": intent_id, "text": text, "created_at": datetime.now(UTC).isoformat(),
                  "issue": issue, "supersedes": supersedes}
        atomic_write_json(record_dir / "intent.json", record, trailing_newline=True)
        if self._audit_log is None:
            from snodo.infrastructure.audit import get_audit_log
            from snodo.project import get_project_id
            project_id, _ = get_project_id(str(self.project_root))
            self._audit_log = get_audit_log(str(self.project_root / ".snodo" / "audit.log"), project_id=project_id)
        self._audit_log.append_event("intent_recorded", {"intent_id": intent_id, "issue": issue, "supersedes": supersedes})
        return record

    def get(self, intent_id: str) -> dict:
        if not isinstance(intent_id, str) or not _ID_RE.fullmatch(intent_id):
            raise IntentError(f"Malformed intent id: {intent_id!r}")
        path = self.directory / intent_id / "intent.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or data.get("id") != intent_id or not isinstance(data.get("text"), str):
                raise ValueError("record fields are invalid")
            return data
        except FileNotFoundError as exc:
            raise IntentError(f"Intent not found: {intent_id}") from exc
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise IntentError(f"Cannot read intent {intent_id}: {exc}") from exc

    def list(self) -> list[dict]:
        if not self.directory.exists():
            return []
        records = [self.get(path.name) for path in self.directory.iterdir() if path.is_dir() and _ID_RE.fullmatch(path.name)]
        return sorted(records, key=lambda item: item["created_at"], reverse=True)
