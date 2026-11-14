"""Collect a demo patch and format the run's final plain-text report."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any


def collect_patch(worktree_folder: str | Path | None, base_commit: str) -> str:
    """Return changes from ``base_commit`` to the worktree's current HEAD."""
    if not worktree_folder:
        return ""
    try:
        result = subprocess.run(  # noqa: S603
            ["git", "diff", f"{base_commit}...HEAD"],  # noqa: S607
            cwd=Path(worktree_folder),
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout


def _display(value: Any) -> str:
    return "n/a" if value is None else str(value)


def format_report(
    record: dict[str, Any], patch_text: str, verification: dict[str, Any]
) -> str:
    """Format the run record, verification result and produced patch."""
    lines = [
        f"Demo run: {record.get('task_id', 'n/a')}",
        f"Arm: {_display(record.get('arm'))}",
        f"Model: {_display(record.get('model'))}",
        f"Outcome: {_display(record.get('outcome'))}",
    ]
    if record.get("outcome") in {"blocked", "halted"}:
        lines.append(f"Halt type: {_display(record.get('halt_type'))}")
    lines.extend(
        [
            f"Time: {_display(record.get('wall_seconds'))} seconds",
            f"Input tokens: {_display(record.get('input_tokens'))}",
            f"Output tokens: {_display(record.get('output_tokens'))}",
            f"Cost: {_display(record.get('cost_usd'))} USD",
            f"Cost source: {_display(record.get('cost_source'))}",
            "Validator findings:",
        ]
    )
    findings = record.get("validator_findings") or []
    if findings:
        lines.extend(
            f"- {_display(item.get('validator_id'))} [{_display(item.get('severity'))}]: "
            f"{str(item.get('justification') or '')[:240]}"
            for item in findings
        )
    else:
        lines.append("- None")
    lines.extend(
        [
            f"Verification: {_display(verification.get('status'))}",
            f"Verification reason: {_display(verification.get('reason'))}",
            "",
            "Patch:",
            patch_text.rstrip() or "(no patch)",
        ]
    )
    return "\n".join(lines) + "\n"
