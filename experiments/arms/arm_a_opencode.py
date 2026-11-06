"""Arm A: opencode, plain prompt — no methodology.

Runs ``opencode run`` inside the instance workspace with the task's
problem_statement as prompt.  Model pinned from config.models.reference.
Patch extracted via ``git add -A && git diff --cached``.
"""

from __future__ import annotations

import json
import re
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
                "opencode",
                "run",
                "--dir",
                str(workspace.path),  # root opencode at the workspace
                "--dangerously-skip-permissions",  # auto-approve edits (non-interactive)
                prompt,  # message is a POSITIONAL for `opencode run`
                "-m",
                model,
            ],
            cwd=str(workspace.path),
            capture_output=True,
            text=True,
            timeout=1800,
        )
        wall_s = time.monotonic() - start

        cost_usd, token_counts = _parse_usage(proc.stdout or "", proc.stderr or "")
        if proc.returncode != 0:
            return _result(
                extract_patch(workspace),
                wall_s,
                cost_usd,
                proc.stderr.strip() or "opencode failed",
                token_counts,
            )

        patch = extract_patch(workspace)
        if not patch:
            combined = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
            return _result(
                patch,
                wall_s,
                cost_usd,
                f"empty patch (rc={proc.returncode}); opencode output tail: {combined[-1000:]}",
                token_counts,
            )
        return _result(patch, wall_s, cost_usd, None, token_counts)
    except FileNotFoundError:
        wall_s = time.monotonic() - start
        return _result("", wall_s, None, "opencode not found")
    except subprocess.TimeoutExpired:
        wall_s = time.monotonic() - start
        return _result("", wall_s, None, "timeout")
    except Exception as exc:
        wall_s = time.monotonic() - start
        return _result("", wall_s, None, str(exc))


def _parse_usage(
    stdout: str, stderr: str = ""
) -> tuple[Optional[float], Optional[dict]]:
    """Extract cost and token counts from opencode JSON or text usage output."""
    text = f"{stdout}\n{stderr}"
    cost = None
    tokens = None
    for line in text.splitlines():
        try:
            item = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            item = None
        if isinstance(item, dict):
            usage = item.get("usage", item)
            if isinstance(usage, dict):
                cost_value = usage.get("cost_usd", usage.get("cost"))
                token_usage = usage.get("tokens", usage)
                if not isinstance(token_usage, dict):
                    token_usage = usage
                if isinstance(cost_value, (int, float)) and not isinstance(
                    cost_value, bool
                ):
                    cost = float(cost_value)
                input_tokens = token_usage.get(
                    "input_tokens",
                    token_usage.get("inputTokens", token_usage.get("input")),
                )
                output_tokens = token_usage.get(
                    "output_tokens",
                    token_usage.get("outputTokens", token_usage.get("output")),
                )
                if isinstance(input_tokens, int) or isinstance(output_tokens, int):
                    tokens = {
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    }
        match = re.search(
            r"(?:cost(?:_usd)?|total cost)\s*[:=]\s*\$?([0-9]+(?:\.[0-9]+)?)",
            line,
            re.I,
        )
        if match:
            cost = float(match.group(1))
        input_match = re.search(r"input(?:_tokens)?\s*[:=]\s*(\d+)", line, re.I)
        output_match = re.search(r"output(?:_tokens)?\s*[:=]\s*(\d+)", line, re.I)
        if input_match or output_match:
            tokens = {
                "input_tokens": int(input_match.group(1)) if input_match else None,
                "output_tokens": int(output_match.group(1)) if output_match else None,
            }
    return cost, tokens


def _result(
    patch: str,
    wall_s: float,
    cost_usd: Optional[float],
    error: Optional[str],
    token_counts: Optional[dict] = None,
) -> Dict[str, Any]:
    result = {
        "patch": patch,
        "wall_s": wall_s,
        "cost_usd": cost_usd,
        "cost_source": "opencode" if cost_usd is not None else "unavailable",
        "error": error,
    }
    if token_counts is not None:
        result.update(token_counts)
    return result


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
