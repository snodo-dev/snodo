"""Arm C: snodo enforced (tokens INV1/INV2/INV3, K-recovery loop).

Runs the snodo engine inside the instance workspace using the same
protocol arm B receives as prose.  ALL internal LLM calls (classifier,
validators, coder) are pinned to the experiment base model.

Branch cleanup is defensive since the workspace is disposable, but
snodo may create task/* branches that need removal.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from snodo.compiler.models import Protocol
from snodo.engine.closure import ClosureNode, run_to_closure
from snodo.engine.loop import build_protocol_graph
from snodo.infrastructure.audit import AuditLog

from experiments.workspace import Workspace, extract_patch


def _snodo_config_with_model(experiment_model: str) -> Path:
    """Create a temporary snodo config dir that pins the experiment model.

    Returns the config dir path.  The caller must set SNODO_HOME to this
    dir before building the graph, and restore afterwards.
    """
    config_dir = Path(tempfile.mkdtemp(prefix="snodo-exp-config-"))
    config_path = config_dir / "config.yml"
    config = {
        "model": experiment_model,
        "engine": {
            "max_subtask_depth": 3,
            "max_session_age_days": 30,
            "token_ttl_seconds": 600,
        },
        "cloud": {
            "api_key": "",
            "api_url": "",
            "sync_enabled": False,
        },
        "llm": {
            "validator": {"model": experiment_model},
            "classifier": {"model": experiment_model},
        },
    }
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
    return config_dir


def run(
    task: dict,
    config: dict,
    run_id: str,
    trial_id: int,
    protocol: Optional[Protocol] = None,
    workspace: Optional[Workspace] = None,
    **kwargs,
) -> Dict[str, Any]:
    """Run arm C (snodo enforced).

    Args:
        task: Task dict from selection.jsonl.
        config: Resolved experiment config.
        run_id: Unique run identifier.
        trial_id: Trial number (1-indexed).
        protocol: Protocol object (same source as arm-B prose).
        workspace: Instance workspace at base_commit.

    Returns:
        Dict with patch, wall_s, cost_usd, error, closure_json.
    """
    if protocol is None:
        return _result("", 0.0, None, "no protocol provided", None)
    if workspace is None:
        return _result("", 0.0, None, "no workspace provided", None)

    project_root = str(workspace.path)
    experiment_model = config["models"]["reference"]
    root_task_id = f"exp1-{task['instance_id']}-{run_id}-t{trial_id}"

    # Stamp environment for K3 traceability
    os.environ["SNODO_TRIAL_ID"] = str(trial_id)
    os.environ["SNODO_RUN_ID"] = run_id

    # Pin ALL snodo LLM calls to the experiment model by setting SNODO_HOME
    # to a temp config dir with the experiment model configured.
    saved_snodo_home = os.environ.pop("SNODO_HOME", None)
    exp_config_dir = _snodo_config_with_model(experiment_model)
    os.environ["SNODO_HOME"] = str(exp_config_dir)

    try:
        # Build and compile graph
        try:
            graph = build_protocol_graph(
                protocol,
                project_root=project_root,
                use_mock_coder=False,
                model=experiment_model,
            )
            compiled = graph.compile()
        except Exception as exc:
            return _result("", 0.0, None, f"graph build failed: {exc}", None)

        # Set up audit log
        audit_log = AuditLog()

        # Run closure
        task_dict = {
            "id": root_task_id,
            "spec": task.get("problem_statement", ""),
            "depth": 0,
        }
        start = time.monotonic()
        try:
            final_state, closure_tree = run_to_closure(
                compiled,
                task_dict,
                mode="producer",
                audit_log=audit_log,
                max_total_fix_attempts=config.get("bounds", {}).get("max_total_fix_attempts", 10),
                max_recovery_depth=config.get("bounds", {}).get("max_recovery_depth", 3),
            )
            wall_s = time.monotonic() - start
        except Exception as exc:
            wall_s = time.monotonic() - start
            return _result("", wall_s, None, f"closure failed: {exc}", None)

        # Extract patch from workspace
        patch = extract_patch(workspace)

        closure_json = _closure_to_dict(closure_tree)
        return _result(patch, wall_s, None, None, closure_json)
    finally:
        # Restore SNODO_HOME
        if saved_snodo_home is not None:
            os.environ["SNODO_HOME"] = saved_snodo_home
        else:
            os.environ.pop("SNODO_HOME", None)
        # Clean up temp config dir
        if exp_config_dir.exists():
            import shutil
            shutil.rmtree(exp_config_dir, ignore_errors=True)


def cleanup_branches(instance_id: str, run_id: str, project_root: Optional[str] = None) -> None:
    """Delete task/* branches matching this run's root task id pattern.

    Only touches branches whose name starts with the exp1 root task id
    pattern for this (instance_id, run_id).  Unrelated task/* branches
    are left untouched.
    """
    root = Path(project_root or Path.cwd())
    try:
        result = subprocess.run(
            ["git", "branch", "--list", "task/*"],
            cwd=root,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return

    pattern = f"exp1-{instance_id}-{run_id}"
    for branch in result.stdout.strip().splitlines():
        branch = branch.strip().lstrip("* ")
        if pattern in branch:
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=root,
                capture_output=True,
            )


def _closure_to_dict(node: ClosureNode) -> dict:
    """Convert a ClosureNode tree to a JSON-serializable dict."""
    return {
        "task_id": node.task_id,
        "depth": node.depth,
        "outcome": node.outcome,
        "spawned_subtasks": node.spawned_subtasks,
        "attempts_used": node.attempts_used,
        "subtasks": [_closure_to_dict(s) for s in node.subtasks],
    }


def _result(
    patch: str,
    wall_s: float,
    cost_usd: Optional[float],
    error: Optional[str],
    closure_json: Optional[dict],
) -> Dict[str, Any]:
    return {
        "patch": patch,
        "wall_s": wall_s,
        "cost_usd": cost_usd,
        "error": error,
        "closure_json": closure_json,
    }


class MockArmC:
    """Mock arm C for testing — returns a synthetic patch + closure."""

    def __init__(self, patch: str = "mock-patch-from-arm-c"):
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
            "wall_s": 0.07,
            "cost_usd": 0.003,
            "error": None,
            "closure_json": {
                "task_id": f"exp1-{task['instance_id']}-{run_id}-t{trial_id}",
                "depth": 0,
                "outcome": "resolved",
                "spawned_subtasks": 0,
                "attempts_used": 1,
                "subtasks": [],
            },
        }
