"""Snodo Developer CLI - Typer-based.

FILE: snodo/cli/main.py

Command implementations live in snodo/cli/commands/*.
This module provides the CLI entry point using Typer.
"""

# ruff: noqa: E402
# The warnings filter must run before any langchain_core import,
# so it sits between stdlib imports and the snodo import block.

import sys
import warnings
from types import SimpleNamespace
from typing import List, Optional

# TODO: remove once langchain_core fixes pydantic v1 detection on 3.14+
# https://github.com/langchain-ai/langchain/issues/33926
warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module="langchain_core",
)

import typer
import click.exceptions
try:
    from typer._click.exceptions import UsageError  # typer vendors its own click
except ImportError:
    from click.exceptions import UsageError

# Re-export command functions and shared utilities so existing imports keep working
from snodo.cli.commands import DEFAULT_PROTOCOL, SOLO_PROTOCOL, TEAM_PROTOCOL, TWO_PLUS_N_PROTOCOL, PROTOCOL_TEMPLATES, load_protocol  # noqa: F401
from snodo.cli.commands.init_cmd import init_command  # noqa: F401
from snodo.cli.commands.config_cmd import config_command  # noqa: F401
from snodo.cli.commands.serve_cmd import serve_command  # noqa: F401
from snodo.cli.commands.plan_cmd import plan_command  # noqa: F401
from snodo.cli.commands.job_cmd import job_command  # noqa: F401
from snodo.cli.commands.agent_cmd import agent_command  # noqa: F401
from snodo.cli.commands.dashboard_cmd import dashboard_command  # noqa: F401
from snodo.cli.commands.sandbox_cmd import sandbox_command  # noqa: F401
from snodo.cli.commands.session_cmd import session_command  # noqa: F401
from snodo.cli.commands.mode_cmd import mode_command  # noqa: F401
from snodo.cli.commands.run_cmd import (  # noqa: F401
    run_command, _execute_task, _fetch_pr_context,
)
from snodo.cli.commands.plan_run import _run_plan  # noqa: F401
from snodo.config import _set_api_key_env  # noqa: F401


app = typer.Typer(
    name="snodo",
    help="Snodo - AI-SDLC Protocol Engine",
    invoke_without_command=True,
)


@app.callback()
def _app_callback(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None, "--version", help="Show version and exit",
        is_eager=True,
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose debug logging",
        is_eager=True,
    ),
):
    """Snodo - AI-SDLC Protocol Engine."""
    if verbose:
        import logging
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(name)s %(levelname)s %(message)s",
        )

    if version:
        from snodo import __version__
        print(f"snodo {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


# === Auto-discovery: mount command modules that expose a top-level `app` ===
# Any snodo/cli/commands/*_cmd.py that defines `app = typer.Typer(...)` and
# optionally `COMMAND_NAME` is automatically mounted here.  Currently only
# mode_cmd qualifies; others will be migrated in subsequent Wave-4 steps.

import pkgutil as _pkgutil
import importlib as _importlib
import snodo.cli.commands as _cli_commands

for _, _mod_name, _ in _pkgutil.iter_modules(_cli_commands.__path__):
    _mod = _importlib.import_module(f"{_cli_commands.__name__}.{_mod_name}")
    _sub_app = getattr(_mod, "app", None)
    if isinstance(_sub_app, typer.Typer):
        _cmd_name = getattr(_mod, "COMMAND_NAME", _mod_name.replace("_cmd", ""))
        app.add_typer(_sub_app, name=_cmd_name)

del _pkgutil, _importlib, _cli_commands, _mod_name, _mod, _sub_app, _cmd_name


# === Init ===

@app.command()
def init(
    template: Optional[str] = typer.Option(
        None, "--template", "-t", help="Protocol template: solo, team, or 2+n",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Overwrite existing .snodo/ directory",
    ),
    mode: Optional[str] = typer.Option(
        None, "--mode", "-m", help="Starting mode (skips interactive picker)",
    ),
):
    """Initialize Snodo project structure."""
    args = SimpleNamespace(template=template, force=force, mode=mode)
    return init_command(args)


# === Run ===

@app.command()
def run(
    description: Optional[str] = typer.Argument(
        None, help="Task description (required unless --plan is used)",
    ),
    protocol: str = typer.Option(
        ".snodo/protocol.yml", "--protocol", help="Path to protocol file",
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="Model to use (e.g., claude-sonnet-4-20250514, gpt-4)",
    ),
    verbose: bool = typer.Option(False, "--verbose", help="Show detailed output"),
    mock: bool = typer.Option(False, "--mock", help="Use mock coder instead of real LLM"),
    plan: Optional[str] = typer.Option(
        None, "--plan", "-p", help="Execute a plan by name",
    ),
    wave: Optional[int] = typer.Option(
        None, "--wave", "-w", help="Execute only a specific wave (requires --plan)",
    ),
    interactive: bool = typer.Option(
        False, "--interactive", "-i", help="Confirm each task before execution",
    ),
    from_pr: Optional[int] = typer.Option(
        None, "--from-pr", help="Fetch PR comments as task context",
    ),
    background: bool = typer.Option(
        False, "--background", "-b", help="Run task in background",
    ),
    sandbox: str = typer.Option(
        "local", "--sandbox", help="Sandbox type: local or docker",
    ),
    resume: Optional[str] = typer.Option(
        None, "--resume", help="Resume execution from session ID",
    ),
    retry: Optional[str] = typer.Option(
        None, "--retry", help="Retry a failed task by ID (requires P0 branch isolation)",
    ),
):
    """Execute a task through the protocol."""
    args = SimpleNamespace(
        description=description, protocol=protocol, model=model,
        verbose=verbose, mock=mock, plan=plan, wave=wave,
        interactive=interactive, from_pr=from_pr, background=background,
        sandbox=sandbox, resume=resume, retry=retry,
    )
    return run_command(args)


# === Serve ===

@app.command()
def serve(
    protocol: str = typer.Option(
        ".snodo/protocol.yml", "--protocol", help="Path to protocol file",
    ),
    mode: Optional[str] = typer.Option(
        None, "--mode", help="Serve a single mode (default: all modes)",
    ),
    transport: str = typer.Option(
        "stdio", "--transport", help="Transport type: stdio, sse, or streamable-http",
    ),
    port: int = typer.Option(55441, "--port", help="Port for SSE/streamable-http transport"),
    tunnel: bool = typer.Option(
        False, "--tunnel", help="Provision a managed Cloudflare tunnel (requires free snodo account)",
    ),
    rotate: bool = typer.Option(
        False, "--rotate", help="Rotate the Cloudflare service token for an existing tunnel",
    ),
    delete: bool = typer.Option(
        False, "--delete", help="Deprovision and remove the managed tunnel",
    ),
    install: bool = typer.Option(
        False, "--install", help="Install MCP servers into Claude Desktop config",
    ),
    uninstall: bool = typer.Option(
        False, "--uninstall", help="Remove this project's MCP entries",
    ),
    uninstall_all: bool = typer.Option(
        False, "--uninstall-all", help="Remove ALL snodo MCP entries",
    ),
    project_name: Optional[str] = typer.Option(
        None, "--project-name", help="Override project name for MCP entry naming",
    ),
):
    """Start MCP server from protocol definition."""
    args = SimpleNamespace(
        protocol=protocol, mode=mode, transport=transport, port=port,
        tunnel=tunnel, rotate=rotate, delete=delete,
        install=install, uninstall=uninstall, uninstall_all=uninstall_all,
        project_name=project_name,
    )
    return serve_command(args)


# === Dashboard ===

@app.command()
def dashboard():
    """Launch the TUI dashboard."""
    from types import SimpleNamespace
    args = SimpleNamespace()
    return dashboard_command(args)


# plan sub-app is now defined in snodo/cli/commands/plan_cmd.py
# and mounted automatically by the discovery loop above.


# === Logs ===

@app.command()
def logs(
    composite_id: str = typer.Argument(..., help="Job ID (j_xxx) or Recon ID (rec_xxx)"),
    watch: bool = typer.Option(False, "--watch", "-w", help="Tail job logs in real time until job completes"),
):
    """Show output for a job or recon by ID."""
    from snodo.cli.commands.logs_cmd import logs_command
    args = SimpleNamespace(composite_id=composite_id, watch=watch)
    return logs_command(args)


# === Meta ===

@app.command()
def meta(
    composite_id: str = typer.Argument(..., help="Job ID (j_xxx) or Task ID (task_xxx)"),
):
    """Show a compact summary for a job or task."""
    from snodo.cli.commands.meta_cmd import meta_command
    args = SimpleNamespace(composite_id=composite_id)
    return meta_command(args)


# === Models ===

@app.command()
def models(
    provider: Optional[str] = typer.Option(None, "--provider", "-p", help="Provider to list models for"),
    flush: bool = typer.Option(False, "--flush", help="Ignore cache and refetch"),
    id_contains: Optional[str] = typer.Option(None, "--id-contains", help="Substring on id/display_name (case-insensitive)"),
    max_output_cost: Optional[float] = typer.Option(None, "--max-output-cost", help="Output cost/1M <= value. Excludes unknown costs."),
    min_output_cost: Optional[float] = typer.Option(None, "--min-output-cost", help="Output cost/1M >= value. Excludes unknown costs."),
    max_input_cost: Optional[float] = typer.Option(None, "--max-input-cost", help="Input cost/1M <= value. Excludes unknown costs."),
    min_context: Optional[int] = typer.Option(None, "--min-context", help="Context window >= value. Excludes context==0."),
):
    """List configured providers and their models."""
    from snodo.cli.commands.models_cmd import models_command
    args = SimpleNamespace(
        provider=provider,
        flush=flush,
        id_contains=id_contains,
        max_output_cost=max_output_cost,
        min_output_cost=min_output_cost,
        max_input_cost=max_input_cost,
        min_context=min_context,
    )
    return models_command(args)


# === Recon ===

@app.command()
def recon(
    query: str = typer.Argument(..., help="The exploration question to answer"),
    paths: Optional[List[str]] = typer.Argument(None, help="Paths to search (default: current directory)"),
    num_agents: Optional[int] = typer.Option(None, "--agents", "-n", help="Number of agents to fan out (uses config if omitted)"),
):
    """Dispatch a read-only exploration query to one or more agents."""
    from snodo.cli.commands.recon_cmd import recon_command
    args = SimpleNamespace(query=query, paths=paths or ["./"], num_agents=num_agents)
    return recon_command(args)


# job sub-app is now defined in snodo/cli/commands/job_cmd.py
# and mounted automatically by the discovery loop above.


# agent sub-app is now defined in snodo/cli/commands/agent_cmd.py
# and mounted automatically by the discovery loop above.


# config sub-app is now defined in snodo/cli/commands/config_cmd.py
# and mounted automatically by the discovery loop above.


# session sub-app is now defined in snodo/cli/commands/session_cmd.py
# and mounted automatically by the discovery loop above.


# mode sub-app is now defined in snodo/cli/commands/mode_cmd.py
# and mounted automatically by the discovery loop above.


# sandbox sub-app is now defined in snodo/cli/commands/sandbox_cmd.py
# and mounted automatically by the discovery loop above.


# === Install / Uninstall ===


@app.command(name="install")
def install_cmd(
    protocol: str = typer.Option(
        ".snodo/protocol.yml", "--protocol", help="Path to protocol file",
    ),
):
    """Install MCP servers into Claude Desktop config."""
    from snodo.cli.commands.install_cmd import install_command
    return install_command(SimpleNamespace(protocol=protocol))


@app.command(name="uninstall")
def uninstall_cmd(
    mode: Optional[str] = typer.Option(
        None, "--mode", help="Remove a single mode entry",
    ),
    all_entries: bool = typer.Option(
        False, "--all", help="Remove ALL snodo-* entries from Claude config",
    ),
    purge: bool = typer.Option(
        False, "--purge", help="Also delete .snodo/ directory and sessions",
    ),
    orphans: bool = typer.Option(
        False, "--orphans", help="Detect and remove orphan MCP entries",
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompts",
    ),
):
    """Remove MCP servers from Claude Desktop config."""
    from snodo.cli.commands.install_cmd import uninstall_command
    return uninstall_command(SimpleNamespace(
        mode=mode, all_entries=all_entries, purge=purge, orphans=orphans, yes=yes,
    ))


# === Authorize (human-only) ===

@app.command()
def authorize(
    task_id: str = typer.Argument(None, help="Task ID of the pending decision to authorize"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    reject_all: bool = typer.Option(False, "--reject-all", help="Bulk reject all pending decisions"),
):
    """Authorize a pending decision (human-only — requires private signing key).

    Reviews the proposal stored by the agent via propose_adjudicate or
    propose_set_model, shows it to the human, and on confirmation mints
    an RS256-signed record.  The agent cannot self-authorize — it has
    no access to the private key.

    When called without a task_id, lists all pending decisions in the active session.
    Use --reject-all to mint signed reject records for all pending decisions at once.
    """
    from snodo.cli.commands.authorize_cmd import authorize_command
    args = SimpleNamespace(task_id=task_id, yes=yes, reject_all=reject_all)
    return authorize_command(args)


# cloud sub-app is now defined in snodo/cli/commands/cloud_cmd.py
# and mounted automatically by the discovery loop above.


# task sub-app is now defined in snodo/cli/commands/task_cmd.py
# and mounted automatically by the discovery loop above.


# === Entry point ===

def main(argv=None):
    """Main CLI entry point.

    Args:
        argv: Command-line arguments (for programmatic/test invocation).
              When None, reads from sys.argv.
    """
    try:
        result = app(args=argv, standalone_mode=False)
        return result if isinstance(result, int) else 0
    except SystemExit:
        raise
    except UsageError as e:
        print(f"Error: {e.format_message()}", file=sys.stderr)
        print("Run 'snodo --help' to see available commands.",
              file=sys.stderr)
        return 2
    except click.exceptions.ClickException:
        raise SystemExit(2)


if __name__ == "__main__":
    sys.exit(main())
