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

import os
import sys
import subprocess
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
    from snodo.mcp.tools import unknown_capability_warnings

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

    protocol_errors = []
    try:
        import yaml
        from snodo.compiler.models import Protocol
        from snodo.compiler.verifier import verify_protocol
        raw = yaml.safe_load(protocol_path.read_text())
        protocol = Protocol(**raw)
        protocol_errors = verify_protocol(protocol).errors
    except Exception:
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
    assessment = assess_readiness(project_root, protocol, protocol_errors=protocol_errors)
    _append_mcp_install_findings(assessment)
    _append_provider_findings(assessment, project_root, protocol)
    protocol_warnings = unknown_capability_warnings(protocol)

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
                "warnings": protocol_warnings,
            },
            EXIT_PASS,
        )

    # Human-readable output formatting
    print(f"Method Scaffolding Readiness: {assessment.score}% ({assessment.passed_checks}/{assessment.total_checks} checks satisfied)")
    if mode_filter:
        print(f"(Displaying findings for mode '{mode_filter}' — readiness score reflects whole protocol)\n")
    else:
        print()

    if protocol_warnings:
        print("Protocol Warnings:")
        for warning in protocol_warnings:
            print(f"  ⚠️ {warning}")
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


def _append_mcp_install_findings(assessment) -> None:
    """Report registered MCP commands that cannot launch this Snodo install."""
    from snodo.readiness.models import FindingSeverity, ReadinessFinding, ReadinessKind
    from snodo.mcp.installer import known_client_targets, _read_target

    for target in known_client_targets():
        config = _read_target(target)
        for name, entry in config.get(target.servers_key, {}).items():
            if not name.startswith("snodo-") or not isinstance(entry, dict):
                continue
            command = entry.get("command", "")
            args = entry.get("args", [])
            local_snodo = (
                isinstance(command, str)
                and Path(command).is_absolute()
                and (Path(command).name in {"snodo", "snodo.exe"}
                     or (isinstance(args, list) and len(args) >= 2
                         and args[0] == "-m" and args[1] == "snodo"))
            )
            # SSH and other wrappers execute on a different machine/context;
            # they cannot be meaningfully imported by this local ready process.
            if not local_snodo:
                continue
            launchable = bool(Path(command).is_file() and os.access(command, os.X_OK))
            reason = None
            if launchable:
                try:
                    result = subprocess.run(  # noqa: S603 - argv list, no shell; absolute interpreter from installed MCP config
                        [command, "-c", "import snodo.cli.main; import snodo.mcp.server"],
                        capture_output=True, text=True, timeout=5, check=False,
                    )
                    if result.returncode != 0:
                        reason = "cannot import Snodo CLI/MCP dependencies"
                except (OSError, subprocess.TimeoutExpired):
                    reason = "cannot execute or import Snodo CLI/MCP dependencies"
            else:
                reason = "local Snodo launcher is not an executable absolute path"
            if reason:
                assessment.workstation_findings.append(ReadinessFinding(
                    id=f"mcp_install_{name}", kind=ReadinessKind.WORKSTATION,
                    severity=FindingSeverity.WARN, modes=["all"],
                    description=(f"Installed MCP entry '{name}' for {target.name} "
                                 f"in {target.config_path} {reason}."),
                    remediation="Reinstall with 'snodo serve --mcp-install' to point at the current installation and refresh its dependencies.",
                 fix_cost=3,
                ))


def _append_provider_findings(assessment, project_root: Path, protocol) -> None:
    """Report installed code-host plugins and this project's provider choice."""
    from snodo.providers.registry import provider_plugin_status, resolve_provider_name, detect_provider
    from snodo.readiness.models import FindingSeverity, ReadinessFinding, ReadinessKind

    metadata = getattr(protocol, "metadata", {}) or {}
    statuses = provider_plugin_status()
    try:
        selected, why = resolve_provider_name(str(project_root), metadata)
        # Instantiation is intentional: readiness must identify a selected plugin
        # whose import succeeded but whose constructor is broken.
        detect_provider(str(project_root), metadata)
        resolution = f"This project resolves to provider '{selected}' ({why})."
    except Exception as exc:
        selected = metadata.get("provider")
        why = f"configured provider '{selected}' failed" if selected else "detected provider failed"
        resolution = f"Provider resolution failed: {type(exc).__name__}: {exc}"
        assessment.workstation_findings.append(ReadinessFinding(
            id="code_host_provider_failure", kind=ReadinessKind.WORKSTATION,
            severity=FindingSeverity.WARN, modes=["all"],
            description=resolution,
            remediation="Fix the provider plugin installation/configuration, then re-run 'snodo ready'.",
            fix_cost=2,
        ))

    lines = [f"Installed code-host plugin '{name}' ({entry['hosts'] or 'no remote_hosts declared'})."
             for name, entry in sorted(statuses.items()) if entry["status"] == "installed"]
    lines.extend(f"Code-host plugin '{name}' failed to load: {entry['error']}"
                 for name, entry in sorted(statuses.items()) if entry["status"] == "failed")
    lines.append(resolution)
    assessment.workstation_findings.append(ReadinessFinding(
        id="code_host_plugins", kind=ReadinessKind.WORKSTATION,
        severity=FindingSeverity.INFO, modes=["all"],
        description=" ".join(lines), remediation="No action required.", fix_cost=1,
    ))
