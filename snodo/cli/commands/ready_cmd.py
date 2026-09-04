"""Ready command — assess method scaffolding readiness relative to configured protocol.

FILE: snodo/cli/commands/ready_cmd.py

Readiness is a property of the method scaffolding relative to the configured
protocol, never of the codebase.

Evaluates the whole protocol (across all modes) deterministically:
1. Repository Readiness (SCORED): What lives in git and travels with the repo
   (decision records, resolvable test command, coder configs, cited paths).
2. Workstation Readiness (REPORTED, UNSCORED): Environment-specific prerequisites
   (binaries on PATH, credentials).

Emits findings as an audit event ('readiness_checked') adhering to the cloud
payload discipline (no absolute paths or machine details).
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
    def ready(
        mode: Optional[str] = typer.Option(
            None, "--mode", "-m", help="Filter displayed findings to those demanded by a specific mode",
        ),
        protocol: str = typer.Option(
            ".snodo/protocol.yml", "--protocol", help="Path to protocol file",
        ),
        json: bool = typer.Option(
            False, "--json", help="Emit machine-readable JSON",
        ),
    ):
        """Assess method scaffolding readiness relative to the configured protocol."""
        return ready_command(SimpleNamespace(mode=mode, protocol=protocol, json=json))

    @app.command(hidden=True)
    def readiness(
        mode: Optional[str] = typer.Option(
            None, "--mode", "-m", help="Filter displayed findings to those demanded by a specific mode",
        ),
        protocol: str = typer.Option(
            ".snodo/protocol.yml", "--protocol", help="Path to protocol file",
        ),
        json: bool = typer.Option(
            False, "--json", help="Emit machine-readable JSON",
        ),
    ):
        """Alias for 'ready' command."""
        return ready_command(SimpleNamespace(mode=mode, protocol=protocol, json=json))


def ready_command(args) -> int:
    """Assess project readiness against the configured protocol."""
    from snodo.cli.json_output import (
        emit_error,
        emit_json,
        schema_name,
        EXIT_INTERNAL_ERROR,
        EXIT_PASS,
    )
    from snodo.infrastructure.audit import get_audit_log
    from snodo.infrastructure.paths import resolve_project_root
    from snodo.project import get_project_id, scope_for_project_id
    from snodo.readiness.checker import assess_readiness

    json_out = getattr(args, "json", False)
    mode_filter = getattr(args, "mode", None)

    project_root_str = resolve_project_root()
    if project_root_str is None:
        if json_out:
            return emit_error("ready", "Not inside a snodo project.", EXIT_INTERNAL_ERROR)
        print("Error: Not inside a snodo project.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    project_root = Path(project_root_str)

    protocol_path = Path(getattr(args, "protocol", ".snodo/protocol.yml"))
    if not protocol_path.is_absolute():
        protocol_path = project_root / protocol_path

    protocol = load_protocol(protocol_path)
    if protocol is None:
        if json_out:
            return emit_error("ready", f"Could not load protocol: {protocol_path}", EXIT_INTERNAL_ERROR)
        print(f"Error: Could not load protocol: {protocol_path}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    if mode_filter and mode_filter not in [m.mode_id for m in protocol.modes]:
        known_modes = ", ".join(m.mode_id for m in protocol.modes)
        if json_out:
            return emit_error("ready", f"Unknown mode '{mode_filter}'. Known modes: {known_modes}", EXIT_INTERNAL_ERROR)
        print(f"Error: Unknown mode '{mode_filter}'. Known modes: {known_modes}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    # Run assessment across the whole protocol
    assessment = assess_readiness(project_root, protocol)

    # Resolve project identity for audit logging
    project_id, _ = get_project_id(str(project_root))
    scope = scope_for_project_id(project_id)
    display_name = project_root.name

    # Construct clean audit payload (relative paths only, no absolute paths or machine details)
    # Repository findings only (workstation findings omitted; count preserved)
    sorted_repo_findings = sorted(
        assessment.repository_findings,
        key=lambda f: (-f.severity.weight(), f.fix_cost, f.id),
    )
    audit_payload = {
        "project_id": project_id,
        "scope": scope,
        "display_name": display_name,
        "protocol_id": assessment.protocol_id,
        "score": assessment.score,
        "total_checks": assessment.total_checks,
        "passed_checks": assessment.passed_checks,
        "repository_findings_count": len(assessment.repository_findings),
        "workstation_findings_count": len(assessment.workstation_findings),
        "findings": [f.to_dict() for f in sorted_repo_findings],
    }

    # Record audit event
    try:
        audit_log = get_audit_log(project_id=project_id)
        if audit_log:
            audit_log.append_event("readiness_checked", audit_payload)
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Failed to append readiness_checked audit event: %s", e)

    if json_out:
        return emit_json(
            {
                "schema": schema_name("ready"),
                "ok": True,
                "project_root": str(project_root),
                "mode_filter": mode_filter,
                "project_id": project_id,
                "scope": scope,
                "display_name": display_name,
                "protocol_id": assessment.protocol_id,
                "score": assessment.score,
                "total_checks": assessment.total_checks,
                "passed_checks": assessment.passed_checks,
                "repository_findings_count": len(assessment.repository_findings),
                "workstation_findings_count": len(assessment.workstation_findings),
                "findings": [f.to_dict() for f in assessment.all_findings],
            },
            EXIT_PASS,
        )

    # Human-readable output formatting
    print(f"Method Scaffolding Readiness: {assessment.score}% ({assessment.passed_checks}/{assessment.total_checks} checks satisfied)")
    if mode_filter:
        print(f"(Displaying findings for mode '{mode_filter}' — readiness score reflects whole protocol)\n")
    else:
        print()

    # Filter findings if mode_filter is set
    repo_findings = [
        f for f in assessment.repository_findings
        if not mode_filter or "all" in f.modes or mode_filter in f.modes
    ]
    # Order cheapest fix at highest severity first
    repo_findings = sorted(repo_findings, key=lambda f: (-f.severity.weight(), f.fix_cost, f.id))

    work_findings = [
        f for f in assessment.workstation_findings
        if not mode_filter or "all" in f.modes or mode_filter in f.modes
    ]
    work_findings = sorted(work_findings, key=lambda f: (-f.severity.weight(), f.fix_cost, f.id))

    print("Repository Readiness (Scored — travels with git repository):")
    if repo_findings:
        for f in repo_findings:
            modes_str = ", ".join(f.modes)
            print(f"  ❌ [{f.severity.value}] {f.description}")
            print(f"     Demanding mode(s): {modes_str}")
            print(f"     Fix: {f.remediation}")
    else:
        print("  ✓ All repository method scaffolding requirements satisfied.")

    print()
    print("Workstation Readiness (Reported — environment specific, unscored):")
    if work_findings:
        for f in work_findings:
            modes_str = ", ".join(f.modes)
            print(f"  ⚠️ [{f.severity.value}] {f.description}")
            print(f"     Demanding mode(s): {modes_str}")
            print(f"     Fix: {f.remediation}")
    else:
        print("  ✓ All workstation requirements satisfied.")

    return EXIT_PASS
