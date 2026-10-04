"""Best-effort, privacy-preserving summaries of a running job's log."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

_ANSI = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
_TAIL_BYTES = 64 * 1024


def job_progress(job_dir: Path) -> dict[str, str]:
    """Return only a fixed-vocabulary activity summary; never forward log text."""
    try:
        path = job_dir / "stdout.log"
        with path.open("rb") as stream:
            stream.seek(0, 2)
            size = stream.tell()
            stream.seek(max(0, size - _TAIL_BYTES))
            text = _ANSI.sub("", stream.read().decode("utf-8", errors="replace"))
        lines = text.splitlines()
        summary = _summarize(lines)
        if not summary:
            return {}
        changed = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        return {"progress": summary[:200], "progress_at": changed}
    except (OSError, ValueError):
        return {}


def _summarize(lines: list[str]) -> str:
    validators: set[str] = set()
    validator_total = 0
    latest = ""
    for raw in lines:
        line = " ".join(raw.split())
        low = line.lower()
        if "validator" in low and any(word in low for word in ("finished", "completed", "passed", "failed")):
            match = re.search(r"(?:validator\s+)?([\w.-]+).*?(?:finished|completed|passed|failed)", low)
            if match:
                validators.add(match.group(1))
            total = re.search(r"(?:of|/)\s*(\d+)\s+validators?", low)
            if total:
                validator_total = int(total.group(1))
            latest = "pre-execute validators"
        if "coder dispatched" in low or "coder working" in low or "coder turn" in low:
            latest = "coder working"
        if "gate" in low and any(word in low for word in ("running", "started", "checking", "verification")):
            latest = "running the gate"
        if "merging into" in low or "merge started" in low:
            latest = "merging into main"
    if latest == "pre-execute validators":
        count = len(validators)
        return f"pre-execute validators: {count} of {validator_total} finished" if validator_total else f"pre-execute validators: {count} finished"
    return latest
