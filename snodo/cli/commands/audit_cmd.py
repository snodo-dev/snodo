"""Audit command group — inspect the tamper-evident record of what ran.

FILE: snodo/cli/commands/audit_cmd.py

``snodo audit verify`` re-opens the project's audit log as an attestation and
reports whether its cryptographic hash chain is intact end to end. The engine
has always exposed ``AuditLog.verify_chain()`` as its integrity gate, but no
command invoked it — so snodo's central claim (a tamper-evident record) could
not be checked by the operator who relies on it, or by anyone auditing a
project. This is that surface (Fixes #176).

It is deliberately additive: it opens the log and verifies it, changing nothing
about how events are written.

A gate nobody sees pass is not a gate, so a passing run prints an explicit OK
with the verified event count rather than exiting in silence.

Exit codes:
    0  chain valid (including an empty log — vacuously intact)
    1  chain INVALID — tampered, corrupted, or memory/file diverge
    4  the check could not run (not inside a project, log unreadable)
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import typer

# ---------------------------------------------------------------------------
# Self-registering Typer app (discovered by snodo/cli/main.py discovery loop)
# ---------------------------------------------------------------------------

COMMAND_NAME = "audit"

app = typer.Typer(invoke_without_command=True, help="Inspect the audit trail")


@app.callback()
def _audit_callback(ctx: typer.Context):
    """Inspect the tamper-evident audit trail."""
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@app.command("verify")
def audit_verify(
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Verify the audit log's hash chain (tamper-evidence)."""
    args = SimpleNamespace(json=json)
    return audit_verify_command(args)


def audit_verify_command(args) -> int:
    """Verify the project's audit-log hash chain and report the verdict."""
    from snodo.cli.json_output import (
        EXIT_INTERNAL_ERROR,
        emit_error,
    )
    from snodo.infrastructure.audit import AuditError, AuditLog
    from snodo.infrastructure.paths import resolve_project_root

    json_out = getattr(args, "json", False)

    project_root = resolve_project_root()
    if project_root is None:
        if json_out:
            return emit_error("audit_verify", "Not inside a snodo project.", EXIT_INTERNAL_ERROR)
        print("Not inside a snodo project.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    log_path = Path(project_root) / ".snodo" / "audit.log"

    # Opening the log validates every line (JSON, sequence, links, hashes) and
    # raises AuditError naming the offending line — that is the operator's
    # "where and why" for a record corrupted on disk. It is a chain failure,
    # not an operational error, so it exits 1 like any other invalid verdict.
    try:
        audit_log = AuditLog(str(log_path))
    except AuditError as err:
        return _report_invalid(json_out, str(log_path), "chain_corrupt", str(err), None, None)
    except OSError as err:
        if json_out:
            return emit_error(
                "audit_verify", f"Could not read audit log {log_path}: {err}",
                EXIT_INTERNAL_ERROR,
            )
        print(f"Error: could not read audit log {log_path}: {err}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    status = audit_log.verify_chain_detailed()
    if status.valid:
        return _report_valid(json_out, str(log_path), status.event_count)
    return _report_invalid(
        json_out, str(log_path), status.reason, status.detail,
        status.event_count, status.sequence,
    )


def _report_valid(json_out: bool, log_path: str, event_count: int) -> int:
    """Emit a passing verdict. A gate must be seen to pass."""
    from snodo.cli.json_output import EXIT_PASS, emit_json, schema_name

    if json_out:
        return emit_json({
            "schema": schema_name("audit_verify"),
            "ok": True,
            "valid": True,
            "log_path": log_path,
            "event_count": event_count,
            "reason": None,
            "sequence": None,
            "detail": _valid_detail(event_count),
        }, EXIT_PASS)

    print("Audit chain: OK")
    print(f"  {log_path}")
    print(f"  {_valid_detail(event_count)}")
    return EXIT_PASS


def _report_invalid(
    json_out: bool, log_path: str, reason: str, detail: str,
    event_count, sequence,
) -> int:
    """Emit a failing verdict naming where the chain broke."""
    from snodo.cli.json_output import EXIT_BLOCKER, emit_json, schema_name

    if json_out:
        # ok=True: the check ran and produced a verdict; valid=False is the
        # verdict itself, mirroring how validate_cmd separates running from the
        # four-outcome status. A consumer branches on "valid".
        return emit_json({
            "schema": schema_name("audit_verify"),
            "ok": True,
            "valid": False,
            "log_path": log_path,
            "event_count": event_count,
            "reason": reason,
            "sequence": sequence,
            "detail": detail,
        }, EXIT_BLOCKER)

    print("Audit chain: FAILED", file=sys.stderr)
    print(f"  {log_path}", file=sys.stderr)
    if detail:
        print(f"  reason: {detail}", file=sys.stderr)
    print(
        "  The audit record cannot be trusted as a faithful record of what ran.",
        file=sys.stderr,
    )
    print(
        "  This is never repaired automatically — inspect, truncate, or archive "
        "the log and start a new chain.",
        file=sys.stderr,
    )
    return EXIT_BLOCKER


def _valid_detail(event_count: int) -> str:
    """Human/JSON one-line summary of a verified chain."""
    if event_count == 0:
        return "No events recorded — the log is empty and vacuously intact."
    return (
        f"{event_count} event(s) verified; hash chain intact and consistent "
        f"with disk."
    )
