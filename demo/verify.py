"""Optional fail-to-pass verification for demo patches."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from typing import Any


def verify_patch(
    instance: dict[str, Any],
    patch: str,
    scorer: Callable[[dict[str, Any], str], dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Verify a patch with the local SWE-bench harness when available."""
    if not patch:
        return {"status": "unresolved", "reason": "no patch"}
    if shutil.which("docker") is None:
        return {"status": "skipped", "reason": "Docker not found"}

    try:
        from experiments import scoring

        row = scoring.get_instance(instance["instance_id"], scoring._LOCAL_DATASET)
        if row is None:
            return {"status": "skipped", "reason": "instance row not found"}
        result = (scorer or scoring.score_prediction)(row, patch)
    except Exception as exc:
        return {"status": "skipped", "reason": f"verification failed: {exc}"}

    resolved = bool(result.get("resolved"))
    return {
        "status": "resolved" if resolved else "unresolved",
        "reason": "fail-to-pass tests passed"
        if resolved
        else "fail-to-pass tests failed",
    }
