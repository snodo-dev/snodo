"""Replay one validator against a recorded Git tree.

The replay model is an explicit override of ADR 039's model precedence: it
replaces a validator's pinned ``model:`` for this measurement only. This
measurement path does not alter gate dispatch or the gate's precedence.
"""

from __future__ import annotations

import logging
import tempfile
import time
from pathlib import Path
from typing import Any

from snodo.core.interfaces import Task
from snodo.validators.runner import resolve_validators, run_validators

logger = logging.getLogger(__name__)


def replay_validator(
    project_root: str | Path,
    protocol: Any,
    mode: str,
    validator_id: str,
    phase: str,
    spec_text: str,
    commit: str,
    base_commit: str,
    model: str,
    completion_fn: Any = None,
    validator_config: Any = None,
) -> dict[str, Any]:
    """Judge *commit* using one validator; return an error record on failure."""
    root = Path(project_root).resolve()
    mode_obj, selected = resolve_validators(protocol, mode, phase)
    pinned = next((v.model for v in selected if v.validator_id == validator_id), None)
    results = []
    usage: dict[str, int | float | None] = {
        "input_tokens": None, "output_tokens": None, "cost": None
    }
    elapsed = 0.0
    worktree: Path | None = None
    repo = None
    try:
        if mode_obj is None:
            raise ValueError(f"Unknown mode: {mode}")
        selected = [v for v in selected if v.validator_id == validator_id]
        if not selected:
            raise ValueError(f"Unknown validator {validator_id!r} for mode {mode!r} and phase {phase!r}")
        if completion_fn is None:
            from snodo.validators.runner import build_completion_fn
            from litellm import completion
            completion_fn = build_completion_fn(model, completion)
        selected = [v.model_copy(update={"model": model}) for v in selected]

        from snodo.tools.git import open_repo
        worktree = Path(tempfile.mkdtemp(prefix="snodo-validator-replay-")) / "tree"
        repo = open_repo(str(root))
        repo.git.worktree("add", "--detach", str(worktree), commit)

        def tracked_completion(*args, **kwargs):
            response = completion_fn(*args, **kwargs)
            from snodo.infrastructure.usage_tracker import usage_tokens_of
            usage["input_tokens"] = _sum(usage["input_tokens"], usage_tokens_of(response, "prompt"))
            usage["output_tokens"] = _sum(usage["output_tokens"], usage_tokens_of(response, "completion"))
            cost_value = getattr(response, "_hidden_params", {}) or {}
            cost = cost_value.get("response_cost") if isinstance(cost_value, dict) else None
            usage["cost"] = _sum(usage["cost"], cost)
            return response

        from snodo.tools.workspace import WorkspaceMCP
        from snodo.tools.git import GitMCP

        workspace = WorkspaceMCP(str(worktree))
        git = GitMCP(str(worktree))
        task = Task(id="", spec=spec_text)
        started = time.monotonic()
        results, _ = run_validators(
            protocol=protocol, validators=selected, task=task, phase=phase,
            completion_fn=tracked_completion, default_model=model,
            validator_config=validator_config, current_mode=mode,
            workspace_mcp=workspace, git_mcp=git,
            base_ref=base_commit, verdict_cache=None,
        )
        elapsed = time.monotonic() - started
        result = results[0] if results else None
        return {
            "validator_id": validator_id, "phase": phase, "commit": commit,
            "base_commit": base_commit, "model": model, "pinned_model": pinned,
            "severity": None if result is None or result.error else result.severity,
            "justification": None if result is None or result.error else result.justification,
            "error": bool(result is None or result.error),
            "blocker": result.justification if result is not None and result.error else None,
            "elapsed_seconds": elapsed, **usage,
        }
    except Exception as exc:  # noqa: BLE001 - replay failures are reportable
        return {
            "validator_id": validator_id, "phase": phase, "commit": commit,
            "base_commit": base_commit, "model": model, "pinned_model": pinned,
            "severity": None, "justification": None, "error": True,
            "blocker": f"Validator replay error ({type(exc).__name__}): {exc}",
            "elapsed_seconds": elapsed, **usage,
        }
    finally:
        if worktree is not None:
            try:
                if repo is None:
                    from snodo.tools.git import open_repo
                    repo = open_repo(str(root))
                repo.git.worktree("remove", "--force", str(worktree))
            except Exception as exc:
                logger.warning("Could not remove validator replay worktree %s: %s", worktree, exc)
            try:
                worktree.parent.rmdir()
            except OSError:
                pass
        if repo is not None:
            repo.close()


def _sum(current: int | float | None, value: int | float | None):
    if value is None:
        return current
    return value if current is None else current + value
