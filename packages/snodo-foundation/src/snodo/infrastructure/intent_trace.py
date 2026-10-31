"""Read plans linked to an intent without coupling the intent store to plans."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

from snodo.compiler.models import Plan
from snodo.infrastructure.intent_store import IntentStore

logger = logging.getLogger(__name__)


def plans_for_intent(project_root: str | Path, intent_id: str) -> list[dict[str, Any]]:
    """Return linked plans and their persisted task-status counts.

    The intent is validated through ``IntentStore``. Individual unreadable or
    malformed plan directories are observationally skipped.
    """
    store = IntentStore(project_root)
    store.get(intent_id)
    plans_dir = store.project_root / ".snodo" / "plans"
    if not plans_dir.is_dir():
        return []

    found = []
    for plan_dir in sorted(plans_dir.iterdir()):
        plan_file = plan_dir / "plan.yml"
        if not plan_dir.is_dir() or not plan_file.is_file():
            continue
        try:
            raw = yaml.safe_load(plan_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
            plan = Plan.from_dict(raw)
            if plan.intent_ref != intent_id:
                continue
            counts: dict[str, int] = {}
            status_file = plan_dir / "status.json"
            if status_file.exists():
                status_data = json.loads(status_file.read_text(encoding="utf-8"))
                if isinstance(status_data, dict) and isinstance(status_data.get("tasks", {}), dict):
                    for entry in status_data.get("tasks", {}).values():
                        status = entry.get("status", "pending") if isinstance(entry, dict) else entry
                        status = str(status)
                        counts[status] = counts.get(status, 0) + 1
            found.append({"name": plan.name or plan_dir.name, "status_counts": counts})
        except Exception as exc:  # malformed plans must not hide other matches
            logger.warning("Skipping unreadable plan %s: %s", plan_dir, exc)
    return found
