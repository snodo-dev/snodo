"""Read task Markdown specs from a project's live or archived plans."""

from __future__ import annotations

import re
from pathlib import Path

MAX_SPEC_BYTES = 64 * 1024
TRUNCATION_MARKER = "\n\n[Task spec truncated: file exceeds 64 KiB.]"
# Task ids carry dots ("1.1_task_spec_reader"), so dots are allowed after the first character.
_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,127}\Z")


def _safe_name(value: str) -> bool:
    return isinstance(value, str) and ".." not in value and _NAME_PATTERN.fullmatch(value) is not None


def read_task_spec(project_path: str | Path, plan_name: str, task_id: str) -> str | None:
    """Return a task's UTF-8 spec, or ``None`` when it cannot safely be read."""
    if not _safe_name(plan_name) or not _safe_name(task_id):
        return None

    root = Path(project_path) / ".snodo"
    for plans_dir in (root / "plans", root / "plans-archive"):
        plan_dir = plans_dir / plan_name
        try:
            base = plan_dir.resolve(strict=True)
            if not base.is_dir() or not base.is_relative_to(plans_dir.resolve(strict=True)):
                continue
            for wave_dir in sorted(plan_dir.glob("wave_*")):
                if not wave_dir.is_dir():
                    continue
                candidate = wave_dir / f"{task_id}_task.md"
                try:
                    resolved = candidate.resolve(strict=True)
                    if not resolved.is_relative_to(base) or not resolved.is_file():
                        continue
                    with resolved.open("rb") as spec_file:
                        data = spec_file.read(MAX_SPEC_BYTES + 1)
                    truncated = len(data) > MAX_SPEC_BYTES
                    if truncated:
                        data = data[:MAX_SPEC_BYTES]
                    text = data.decode("utf-8")
                    return text + TRUNCATION_MARKER if truncated else text
                except (OSError, UnicodeError):
                    continue
        except OSError:
            continue
    return None
