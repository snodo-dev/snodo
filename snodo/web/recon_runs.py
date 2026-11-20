"""Read recon run state for the local dashboard without changing project files."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any


def project_recon_runs(project_path: str | Path, now: datetime | float) -> list[dict[str, Any]]:
    """Return valid recon records with effective dashboard states."""
    from snodo.recon import RECON_DEFAULT_DEADLINE_SECONDS

    if isinstance(now, datetime):
        current = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).timestamp()
    else:
        current = float(now)
    records: list[dict[str, Any]] = []
    try:
        recon_dirs = (Path(project_path) / ".snodo" / "recons").iterdir()
        for directory in recon_dirs:
            if not directory.is_dir():
                continue
            try:
                with (directory / "state.json").open(encoding="utf-8") as stream:
                    state = json.load(stream)
            except (OSError, ValueError):
                continue
            if not isinstance(state, dict):
                continue
            status, created = state.get("status"), state.get("created_at")
            if status not in {"running", "complete", "failed"} or not isinstance(created, (int, float)):
                continue
            if status == "running":
                pid = state.get("pid")
                alive = False
                usable_pid = isinstance(pid, int) and not isinstance(pid, bool) and pid > 0
                if usable_pid:
                    try:
                        os.kill(pid, 0)
                        alive = True
                    except PermissionError:
                        alive = True
                    except ProcessLookupError:
                        pass
                    except OSError:
                        pass
                effective = "running" if alive else (
                    "running" if not usable_pid and current - created <= RECON_DEFAULT_DEADLINE_SECONDS
                    else "stale"
                )
            else:
                effective = "completed" if status == "complete" else "failed"
            records.append({
                "recon_id": str(state.get("recon_id") or directory.name),
                "state": effective,
                "created_at": created,
                "completed_at": state.get("completed_at"),
                "agents": state.get("agents", []),
            })
    except OSError:
        return []
    return records
