"""Read-only queue validation report (ADR 053)."""

from __future__ import annotations

import sys
from pathlib import Path

import typer

from snodo.infrastructure.paths import require_project_root
from snodo.infrastructure.queue_validation import build_validation_report

# Attached to the single `snodo queue` group owned by queue_cmd; defining a
# second Typer named "queue" here would replace that group at discovery.
from snodo.cli.commands import queue_cmd as _queue_group


@_queue_group.app.command("validate")
def queue_validate(
    queue: str | None = typer.Argument(None, help="Queue name (all queues when omitted)"),
    json_output: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
) -> int:
    """Report queue readiness, verification, dependencies, and active runners."""
    try:
        root = Path(require_project_root())
        report = build_validation_report(root, queue)
    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    if json_output:
        from snodo.cli.json_output import emit_json, schema_name

        report["schema"] = schema_name("queue_validate")
        return emit_json(report)

    _print_report(report)
    return 0


def _print_report(report: dict) -> None:
    for name, queue in report["queues"].items():
        print(f"Queue {name}: runner {'active' if queue['runner_active'] else 'not active'}")
        front = queue["front"]
        if front is None:
            print("  Empty")
        elif queue["runnable"]:
            print(f"  Front plan {front['name']} is runnable")
        else:
            stopped = front["stopped_by"]
            if stopped:
                reason = f": {stopped['reason']}" if stopped["reason"] else ""
                print(f"  Stopped at {front['name']}: task {stopped['task']} is {stopped['status']}{reason}")
            else:
                print(f"  Stopped at {front['name']}: plan verification failed")
            for error in front["verification_errors"]:
                print(f"    - {error}")
        for plan in queue["plans"]:
            if not plan["verified"]:
                print(f"  Plan {plan['name']} no longer verifies:")
                for error in plan["verification_errors"]:
                    print(f"    - {error}")
        for problem in queue["order_problems"]:
            print(f"  Order problem: {problem['plan']} cites {problem['path']} ({problem['type']})")
    for warning in report["cross_queue_warnings"]:
        print(f"Warning: queues {', '.join(warning['queues'])} both touch {warning['path']}")
