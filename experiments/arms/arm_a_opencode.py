"""Arm A: opencode, plain prompt — no methodology.

Runs ``opencode run`` inside the instance workspace with the task's
problem_statement as prompt.  Model pinned from config.models.reference.
Patch extracted via ``git add -A && git diff --cached``.
"""

from __future__ import annotations

import subprocess
import time
from typing import Any, Dict, Optional

from experiments.workspace import Workspace, extract_patch


def run(
    task: dict,
    config: dict,
    run_id: str,
    trial_id: int,
    workspace: Optional[Workspace] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Run arm A (opencode, plain).

    Args:
        task: Task dict from selection.jsonl.
        config: Resolved experiment config.
        run_id: Unique run identifier.
        trial_id: Trial number (1-indexed).
        workspace: Instance workspace at base_commit.

    Returns:
        Dict with patch, wall_s, cost_usd, error.
    """
    if workspace is None:
        return _result("", 0.0, None, "no workspace provided")

    model = config["models"]["reference"]
    prompt = task.get("problem_statement", "")

    if not prompt:
        return _result("", 0.0, None, "empty problem_statement")

    start = time.monotonic()
    try:
        proc = subprocess.run(
            [
                "opencode", "run",
                "--dir", str(workspace.path),       # root opencode at the workspace
                "--dangerously-skip-permissions",   # auto-approve edits (non-interactive)
                prompt,            # message is a POSITIONAL for `opencode run`
                "-m", model,
            ],
            cwd=str(workspace.path),
            capture_output=True,
            text=True,
            timeout=600,
        )
        wall_s = time.monotonic() - start

        if proc.returncode != 0:
            return _result(
                extract_patch(workspace), wall_s, None,
                proc.stderr.strip() or "opencode failed",
            )

        patch = extract_patch(workspace)
        if not patch:
            combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            return _result(
                patch, wall_s, None,
                f"empty patch (rc={proc.returncode}); opencode output tail: {combined[-1000:]}",
            )
        return _result(patch, wall_s, None, None)
    except FileNotFoundError:
        wall_s = time.monotonic() - start
        return _result("", wall_s, None, "opencode not found")
    except subprocess.TimeoutExpired:
        wall_s = time.monotonic() - start
        return _result("", wall_s, None, "timeout")
    except Exception as exc:
        wall_s = time.monotonic() - start
        return _result("", wall_s, None, str(exc))


def _result(
    patch: str,
    wall_s: float,
    cost_usd: Optional[float],
    error: Optional[str],
) -> Dict[str, Any]:
    return {
        "patch": patch,
        "wall_s": wall_s,
        "cost_usd": cost_usd,
        "error": error,
    }


class MockArmA:
    """Mock arm A for testing — returns a synthetic patch."""

    def __init__(self, patch: str = "mock-patch-from-arm-a"):
        self._patch = patch

    def run(
        self,
        task: dict,
        config: dict,
        run_id: str,
        trial_id: int,
        **kwargs,
    ) -> Dict[str, Any]:
        return {
            "patch": self._patch,
            "wall_s": 0.05,
            "cost_usd": 0.001,
            "error": None,
        }
