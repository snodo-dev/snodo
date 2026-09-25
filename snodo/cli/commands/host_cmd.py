"""Remote execution host inspection commands (ADR 055)."""

import os
import sys
from pathlib import Path

import yaml

import typer

from snodo.cli.json_output import emit_json, schema_name
from snodo.remote_host import (
    HostCheck, check_remote_host, host_check_json, resolve_host_path,
    resolve_task_provider_keys, select_execution_host,
)

COMMAND_NAME = "host"
app = typer.Typer(help="Inspect the configured remote execution host")


def _check_summary(check: HostCheck, host_path: str | None = None) -> str:
    if check.name == "reachable":
        return "SSH connection established" if check.ok else "SSH connection failed"
    if check.name == "version":
        return "snodo version matches" if check.ok else "snodo version mismatch"
    if check.name == "project_clone":
        if not check.ok:
            return "project clone check failed"
        remote = check.detail.partition("expected remote ")[2].partition("; got")[0].strip("'")
        path = host_path or "project clone"
        return f"{path} tracks {remote}" if remote else f"{path} matches the project origin"
    if check.name.startswith("provider_key:"):
        return check.detail or ("provider key available" if check.ok else "provider key unavailable")
    return check.name.replace("_", " ")


def _render_check(check: HostCheck, *, host_path: str | None = None,
                   verbose: bool = False, color: bool = False) -> list[str]:
    mark = "✓" if check.ok else "✗"
    if color:
        mark = f"\033[32m{mark}\033[0m" if check.ok else f"\033[31m{mark}\033[0m"
    lines = [f"{mark} {check.name}  {_check_summary(check, host_path)}"]
    if verbose or not check.ok:
        lines.extend((f"    command: {check.command}", f"    {check.detail}"))
    return lines


def _print_check_results(checks: list[HostCheck], *, host_path: str | None = None,
                         verbose: bool = False) -> None:
    color = bool(getattr(sys.stdout, "isatty", lambda: False)()) and "NO_COLOR" not in os.environ
    for check in checks:
        for line in _render_check(check, host_path=host_path, verbose=verbose, color=color):
            print(line)
    failed = sum(not check.ok for check in checks)
    print("Host ready" if failed == 0 else f"{failed} checks failed")


@app.command("check")
def host_check(
    json_output: bool = typer.Option(False, "--json", help="Print machine-readable results"),
    verbose: bool = typer.Option(False, "--verbose", help="Show details for every check"),
):
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
    parsed_protocol = None
    if protocol_path.is_file():
        from snodo.cli.commands import load_protocol
        parsed_protocol = load_protocol(protocol_path)
    provider_checks = []
    if parsed_protocol is not None:
        from snodo.config import ConfigManager
        manager = ConfigManager()
        try:
            _keys, provider_checks = resolve_task_provider_keys(
                parsed_protocol, manager.get_coder_model(), parsed_protocol.initial_mode,
            )
        except Exception as exc:
            provider_checks = [HostCheck(
                "provider_keys", "resolve locally", False,
                f"Unable to resolve task loop providers: {exc}",
            )]
    execution = protocol.get("execution", {})
    if not isinstance(execution, dict):
        execution = {}
    host = select_execution_host(execution)
    if not host:
        result = {
            "schema": schema_name("host.check"), "host": None,
            **host_check_json(provider_checks),
            "message": "No remote host configured; execution is local.",
        }
        if json_output:
            return emit_json(result)
        else:
            print(result["message"])
            _print_check_results(provider_checks, verbose=verbose)
        return 0 if result["ok"] else 1

    checks = [*check_remote_host(host, project_path=".", host_path=execution.get("host_path")), *provider_checks]
    result = {"schema": schema_name("host.check"), "host": host, **host_check_json(checks)}
    if json_output:
        return emit_json(result, exit_code=0 if result["ok"] else 1)
    else:
        try:
            display_path = resolve_host_path(".", execution.get("host_path"))
            if display_path.startswith(str(Path.home())):
                display_path = "~" + display_path[len(str(Path.home())):]
        except ValueError:
            display_path = execution.get("host_path")
        _print_check_results(checks, host_path=display_path, verbose=verbose)
    return 0 if result["ok"] else 1
