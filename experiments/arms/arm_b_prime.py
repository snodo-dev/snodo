"""Arm B' (b-prime): advisory-feedback control for the compute/information confound.

Arm C differs from arm B in THREE ways at once: (i) structural enforcement
(the diff is gated), (ii) extra inference budget (up to K coder attempts), and
(iii) task-specific validator feedback ("this diff is too large, tighten it").
A reviewer can therefore attribute the C > B gap to best-of-N-with-critique
(a known effect) rather than to enforcement per se.

Arm B' isolates enforcement by giving arm B the SAME budget and the SAME
validator critique, but ADVISORY and NON-BLOCKING:
  * same coder path as arm B (opencode + prose methodology),
  * after each attempt, the SAME validator model + minimality criteria as arm C
    review the diff and emit a critique,
  * if flagged, the coder is re-invoked with the critique appended (up to
    max_attempts), but it is free to ignore it and the FINAL patch is always
    accepted -- nothing is gated or withheld.

Interpretation of the paired comparison on the same tasks:
  * C > B'  -> the structural/binding part adds value beyond the feedback loop.
  * C ~= B' -> the gain is the iterative critique, obtainable without enforcement;
               the contribution reframes to "the language makes that loop
               structural/guaranteed" rather than "enforcement raises the ceiling".

The validator is called via litellm with the same model string arm C uses
(SNODO_VALIDATOR_MODEL); on any validator error we treat the verdict as PASS
and stop looping, so B' degrades gracefully to B (never crashes the run).
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any, Dict, List, Optional

from experiments.workspace import Workspace, extract_patch

# Same minimality criteria the surgeon protocol's post-execute validator applies
# in arm C. Kept in sync deliberately so B' and C judge diffs identically.
_MINIMALITY_CRITERIA = (
    "You are reviewing a code diff that fixes a reported issue. Enforce MINIMALITY: "
    "the correct fix is the SMALLEST change that makes the reported failure pass. "
    "Flag the diff (verdict TIGHTEN) if it touches files unrelated to the defect, "
    "refactors/renames/reformats, adds functionality beyond the fix, or is large "
    "relative to the defect. Do NOT require new tests. Judge ONLY minimality and "
    "localisation."
)

_MAX_DIFF_CHARS = 6000


def _validator_api_key(provider: str) -> Optional[str]:
    """Best-effort load of the provider API key from the snodo config, so the
    direct litellm call authenticates the same way arm C's validators do."""
    try:
        from snodo.config import ConfigManager
        provs = ConfigManager().load().get("providers", {}) or {}
        p = provs.get(provider, {}) or {}
        return p.get("api_key") or p.get("apiKey") or p.get("key")
    except Exception:
        return None


def _review_diff(problem: str, diff: str, validator_model: str) -> tuple[bool, str]:
    """Ask the validator model whether the diff is minimal. Returns
    (tighten, critique). On any error returns (False, "") so the loop stops
    and B' degrades to B."""
    if not diff.strip():
        return (False, "")
    provider = validator_model.split("/", 1)[0]
    api_key = _validator_api_key(provider)
    prompt = (
        f"{_MINIMALITY_CRITERIA}\n\n"
        f"## Reported issue\n{problem[:2000]}\n\n"
        f"## Diff\n{diff[:_MAX_DIFF_CHARS]}\n\n"
        "Respond with exactly one line 'VERDICT: PASS' or 'VERDICT: TIGHTEN', "
        "then (if TIGHTEN) a brief bulleted critique of what to remove or minimise."
    )
    try:
        from litellm import completion
        resp = completion(
            model=validator_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            api_key=api_key,
            timeout=120,
        )
        text = (resp["choices"][0]["message"]["content"] or "").strip()
    except Exception:
        return (False, "")
    tighten = "VERDICT: TIGHTEN" in text.upper()
    return (tighten, text)


def _run_coder(workspace: Workspace, prompt: str, model: str) -> tuple[int, str, str]:
    """One opencode invocation on the workspace (identical to arm B's call)."""
    proc = subprocess.run(
        ["opencode", "run", "--dir", str(workspace.path),
         "--dangerously-skip-permissions", prompt, "-m", model],
        cwd=str(workspace.path), capture_output=True, text=True, timeout=1800,
    )
    return proc.returncode, (proc.stdout or ""), (proc.stderr or "")


def run(
    task: dict,
    config: dict,
    run_id: str,
    trial_id: int,
    prose: str = "",
    workspace: Optional[Workspace] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Run arm B' (advisory feedback loop, non-blocking)."""
    if workspace is None:
        return _result("", 0.0, None, "no workspace provided", None)

    model = config["models"]["reference"]
    problem = task.get("problem_statement", "")
    if not problem:
        return _result("", 0.0, None, "empty problem_statement", None)

    validator_model = os.environ.get("SNODO_VALIDATOR_MODEL") or model
    max_attempts = int(config.get("bounds", {}).get("bprime", {}).get("max_attempts", 4))

    base_prompt = f"{prose}\n\n---\n\n{problem}"
    verdicts: List[str] = []
    last_err: Optional[str] = None
    start = time.monotonic()

    try:
        prompt = base_prompt
        for attempt in range(1, max_attempts + 1):
            try:
                rc, out, err = _run_coder(workspace, prompt, model)
            except FileNotFoundError:
                return _result("", time.monotonic() - start, None, "opencode not found", None)
            except subprocess.TimeoutExpired:
                last_err = "timeout"
                break
            if rc != 0:
                last_err = (err.strip() or "opencode failed")

            diff = extract_patch(workspace)
            # Advisory review — never blocks; only decides whether to offer another round.
            tighten, critique = _review_diff(problem, diff, validator_model)
            verdicts.append("tighten" if tighten else "pass")
            if not tighten or attempt == max_attempts:
                break
            # Re-invoke the coder with the critique as ADVICE (free to ignore).
            prompt = (
                f"{base_prompt}\n\n---\n"
                f"A reviewer flagged your current change as not minimal:\n{critique}\n\n"
                "Revise the change in place to be as small and localised as possible: "
                "drop unrelated edits, avoid refactors/renames/reformatting, keep only "
                "what makes the failing test pass. If you believe it is already minimal, "
                "leave it unchanged."
            )

        wall_s = time.monotonic() - start
        patch = extract_patch(workspace)
        info = {
            "outcome": "resolved" if patch else "empty",
            "attempts_used": len(verdicts),
            "verdicts": verdicts,
            "advisory": True,
        }
        if not patch:
            return _result("", wall_s, None, last_err or f"empty patch (advisory, attempts={len(verdicts)})", info)
        return _result(patch, wall_s, None, None, info)
    except Exception as exc:
        return _result(extract_patch(workspace), time.monotonic() - start, None, str(exc), None)


def _result(patch, wall_s, cost_usd, error, closure_json) -> Dict[str, Any]:
    return {"patch": patch, "wall_s": wall_s, "cost_usd": cost_usd,
            "error": error, "closure_json": closure_json}


class MockArmBPrime:
    """Mock arm B' for testing — synthetic patch + advisory closure."""

    def __init__(self, patch: str = "mock-patch-from-arm-bprime"):
        self._patch = patch

    def run(self, task, config, run_id, trial_id, **kwargs) -> Dict[str, Any]:
        return {"patch": self._patch, "wall_s": 0.06, "cost_usd": 0.002, "error": None,
                "closure_json": {"outcome": "resolved", "attempts_used": 2,
                                 "verdicts": ["tighten", "pass"], "advisory": True}}
