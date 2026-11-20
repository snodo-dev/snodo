"""Tests for the read-only local recon run reader."""

from __future__ import annotations

import json
import os
from pathlib import Path

from snodo.recon import RECON_DEFAULT_DEADLINE_SECONDS
from snodo.web.recon_runs import project_recon_runs


def _record(root: Path, name: str, **values: object) -> None:
    path = root / ".snodo" / "recons" / name / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "recon_id": name, "status": "running", "created_at": 1000,
        "completed_at": None, "pid": None, "agents": ["lane"], **values,
    }), encoding="utf-8")


def test_running_pid_deadline_terminal_and_malformed(tmp_path: Path) -> None:
    now = 1000 + RECON_DEFAULT_DEADLINE_SECONDS + 1
    _record(tmp_path, "live", pid=os.getpid())
    _record(tmp_path, "dead", pid=999999999)
    _record(tmp_path, "within", pid=None, created_at=now - RECON_DEFAULT_DEADLINE_SECONDS)
    _record(tmp_path, "expired", pid=None, created_at=1000)
    _record(tmp_path, "complete", status="complete", completed_at=1100)
    _record(tmp_path, "failed", status="failed", completed_at=1100)
    broken = tmp_path / ".snodo" / "recons" / "broken" / "state.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{", encoding="utf-8")

    result = {run["recon_id"]: run for run in project_recon_runs(tmp_path, now)}

    assert {key: value["state"] for key, value in result.items()} == {
        "live": "running", "dead": "stale", "within": "running",
        "expired": "stale", "complete": "completed", "failed": "failed",
    }
    assert result["live"]["agents"] == ["lane"]
