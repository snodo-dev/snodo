"""Validate command — run a phase's validators and return the structured result.

FILE: snodo/cli/commands/validate_cmd.py

The machine-interface entry point for validation (ADR 022).  It runs the same
shared validator runner the engine and the MCP server use, evaluates the
disagreement policy, and returns the four-outcome result as JSON with an exit
code a caller can branch on — without running a coder.

The JSON shape mirrors the engine's halt payload: ``status`` (one of pass /
escalate / blocker / validator_error), ``results`` (per-validator verdicts),
and ``policy_decision``.  Exit codes: pass=0, blocker=1, escalate=2,
validator_error=3, internal_error=4.
"""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import typer

from snodo.cli.commands import load_protocol


def register(app: typer.Typer) -> None:
    """Register top-level CLI commands onto app (called by discovery loop)."""

    @app.command()
    def validate(
        task_spec: str = typer.Argument(..., help="Task spec to validate"),
        phase: str = typer.Option(
            "pre_execute", "--phase", help="Validation phase (pre_execute or post_execute)",
        ),
        protocol: str = typer.Option(
            ".snodo/protocol.yml", "--protocol", help="Path to protocol file",
        ),
        mode: Optional[str] = typer.Option(
            None, "--mode", help="Mode to validate in (default: active mode)",
        ),
        json: bool = typer.Option(True, "--json/--no-json", help="Emit machine-readable JSON"),
    ):
        """Run a phase's validators and return the structured result."""
        args = SimpleNamespace(
            task_spec=task_spec, phase=phase, protocol=protocol, mode=mode, json=json,
        )
        return validate_command(args)


def validate_command(args) -> int:
    """Run validators for a phase and return the four-outcome result."""
    from snodo.cli.json_output import (
        emit_json, emit_error, schema_name, OUTCOME_EXIT_CODES, EXIT_INTERNAL_ERROR,
    )
    from snodo.infrastructure.paths import resolve_project_root

    json_out = getattr(args, "json", True)
    task_spec = getattr(args, "task_spec", "")
    phase = getattr(args, "phase", "pre_execute")

    if not task_spec:
        if json_out:
            return emit_error("validate", "task_spec required", EXIT_INTERNAL_ERROR)
        print("Error: task spec required", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    project_root = resolve_project_root()
    if project_root is None:
        if json_out:
            return emit_error("validate", "Not inside a snodo project.", EXIT_INTERNAL_ERROR)
        print("Error: Not inside a snodo project.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    protocol_path = Path(getattr(args, "protocol", ".snodo/protocol.yml"))
    if not protocol_path.is_absolute():
        protocol_path = Path(project_root) / protocol_path
    protocol = load_protocol(protocol_path)
    if protocol is None:
        if json_out:
            return emit_error("validate", f"Could not load protocol: {protocol_path}", EXIT_INTERNAL_ERROR)
        return EXIT_INTERNAL_ERROR

    # Resolve the mode: explicit --mode, else the active mode, else initial.
    mode_id = getattr(args, "mode", None)
    if not mode_id:
        from snodo.infrastructure.state import read_state
        state = read_state(project_root)
        mode_id = state.current_mode or protocol.initial_mode

    from snodo.validators.runner import (
        classify_outcome,
        resolve_validator_completion,
        resolve_validators,
        run_validators,
    )
    from snodo.engine.policy import policy_decision_to_dict
    from snodo.tools.workspace import WorkspaceMCP
    from snodo.tools.git import GitMCP

    mode, validators = resolve_validators(protocol, mode_id, phase)
    if mode is None:
        if json_out:
            return emit_error("validate", f"Unknown mode: {mode_id}", EXIT_INTERNAL_ERROR)
        print(f"Error: Unknown mode: {mode_id}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    if not validators:
        if json_out:
            return emit_error(
                "validate",
                f"No {phase} validators for mode '{mode_id}'.",
                EXIT_INTERNAL_ERROR,
            )
        print(f"No {phase} validators for mode '{mode_id}'.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # Resolve the validator LLM. Failure → validator_error (not a pass).
    try:
        completion_fn, validator_model, validator_config = resolve_validator_completion()
    except Exception as e:  # noqa: BLE001
        if json_out:
            return emit_json({
                "schema": schema_name("validate"),
                "ok": True,
                "status": "validator_error",
                "task_id": "",
                "phase": phase,
                "mode": mode_id,
                "results": [{
                    "validator_id": "config",
                    "severity": "blocker",
                    "justification": f"Could not resolve validator LLM: {e}",
                }],
                "policy_decision": None,
                "instruction": "Could not resolve validator LLM — retry or inspect logs.",
            }, OUTCOME_EXIT_CODES["validator_error"])
        print(f"Error: Could not resolve validator LLM: {e}", file=sys.stderr)
        return OUTCOME_EXIT_CODES["validator_error"]

    from snodo.core.interfaces import Task
    from snodo.paths import derive_task_id

    task = Task(id=derive_task_id(task_spec), spec=task_spec)

    workspace = WorkspaceMCP(project_root)
    git = GitMCP(project_root)

    results, _ = run_validators(
        protocol=protocol,
        validators=validators,
        task=task,
        phase=phase,
        completion_fn=completion_fn,
        default_model=validator_model,
        validator_config=validator_config,
        workspace_mcp=workspace,
        git_mcp=git,
        current_mode=mode_id,
        session_id="",
        audit_log=None,
    )

    from snodo.engine.policy import PolicyEvaluator
    decision = PolicyEvaluator().evaluate(
        results, protocol.disagreement_policy, "pre_execute", task_ref=task.id,
    )
    status = classify_outcome(results, decision)

    serialized = [
        {"validator_id": r.validator_id, "severity": r.severity,
         "justification": r.justification}
        for r in results
    ]

    payload = {
        "schema": schema_name("validate"),
        "ok": True,
        "status": status,
        "task_id": task.id,
        "phase": phase,
        "mode": mode_id,
        "results": serialized,
        "policy_decision": policy_decision_to_dict(decision),
        "instruction": _instruction(status),
    }

    if json_out:
        return emit_json(payload, OUTCOME_EXIT_CODES.get(status, EXIT_INTERNAL_ERROR))

    # Human output: a compact summary of the same result.
    print(f"Validation ({phase}, mode={mode_id}): {status}")
    for r in serialized:
        print(f"  {r['validator_id']} [{r['severity']}]: {r['justification']}")
    return OUTCOME_EXIT_CODES.get(status, EXIT_INTERNAL_ERROR)


def _instruction(status: str) -> str:
    """Return the follow-up instruction for a validation outcome."""
    if status == "pass":
        return "Validation passed. The task may proceed to execution."
    if status == "escalate":
        return "Human review required. Run: snodo authorize <decision_id>."
    if status == "blocker":
        return "Blockers present. Fix the code and re-validate; if exhausted, revise the spec."
    if status == "validator_error":
        return "A validator failed to produce a verdict. Retry or inspect logs."
    return ""
