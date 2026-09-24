"""Remote execution host inspection commands (ADR 055)."""

from pathlib import Path

import yaml

import typer

from snodo.cli.json_output import emit_json, schema_name
from snodo.remote_host import check_remote_host, host_check_json, select_execution_host

COMMAND_NAME = "host"
app = typer.Typer(help="Inspect the configured remote execution host")


@app.command("check")
def host_check(json_output: bool = typer.Option(False, "--json", help="Print machine-readable results")):
    """Check the selected SSH host before remote execution."""
    protocol_path = Path(".snodo/protocol.yml")
    try:
        protocol = yaml.safe_load(protocol_path.read_text()) if protocol_path.is_file() else {}
    except (OSError, yaml.YAMLError) as exc:
        result = {"schema": schema_name("host.check"), "ok": False, "error": str(exc)}
        if json_output:
            return emit_json(result, exit_code=1)
        print(f"Unable to read {protocol_path}: {exc}")
        return 1
    protocol = protocol or {}
    execution = protocol.get("execution", {})
    if not isinstance(execution, dict):
        execution = {}
    host = select_execution_host(execution)
    if not host:
        result = {"schema": schema_name("host.check"), "ok": True, "host": None, "checks": [], "message": "No remote host configured; execution is local."}
        if json_output:
            return emit_json(result)
        else:
            print(result["message"])
        return 0

    checks = check_remote_host(host, project_path=".", host_path=execution.get("host_path"))
    result = {"schema": schema_name("host.check"), "host": host, **host_check_json(checks)}
    if json_output:
        return emit_json(result, exit_code=0 if result["ok"] else 1)
    else:
        for check in checks:
            status = "ok" if check.ok else "FAILED"
            detail = f": {check.detail}" if check.detail else ""
            print(f"{check.name}: {status} — {check.command}{detail}")
    return 0 if result["ok"] else 1
