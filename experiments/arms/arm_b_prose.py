"""Arm B: opencode + protocol-as-prose methodology context.

Same harness as arm A, but the prompt includes the snodo protocol
exported as prose instructions.  No runtime enforcement.
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
    prose: str = "",
    workspace: Optional[Workspace] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Run arm B (opencode + prose methodology).

    Args:
        task: Task dict from selection.jsonl.
        config: Resolved experiment config.
        run_id: Unique run identifier.
        trial_id: Trial number (1-indexed).
        prose: Protocol-as-prose methodology content (from parity gate).
        workspace: Instance workspace at base_commit.

    Returns:
        Dict with patch, wall_s, cost_usd, error.
    """
    if workspace is None:
        return _result("", 0.0, None, "no workspace provided")

    model = config["models"]["reference"]
    problem = task.get("problem_statement", "")

    if not problem:
        return _result("", 0.0, None, "empty problem_statement")

    # Prepend prose methodology to the problem statement
    prompt = f"{prose}\n\n---\n\n{problem}"

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


class MockArmB:
    """Mock arm B for testing — returns a synthetic patch."""

    def __init__(self, patch: str = "mock-patch-from-arm-b"):
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
            "wall_s": 0.06,
            "cost_usd": 0.002,
            "error": None,
        }
