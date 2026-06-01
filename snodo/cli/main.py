"""Snodo Developer CLI - Typer-based.

FILE: snodo/cli/main.py

Command implementations live in snodo/cli/commands/*.
This module provides the CLI entry point using Typer.
"""

import sys
from types import SimpleNamespace
from typing import Optional

import typer
import click.exceptions

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
    run_command, _execute_task, _run_plan, _set_api_key_env, _fetch_pr_context,
)


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
):
    """Snodo - AI-SDLC Protocol Engine."""
    if version:
        from snodo import __version__
        print(f"snodo {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


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
):
    """Execute a task through the protocol."""
    args = SimpleNamespace(
        description=description, protocol=protocol, model=model,
        verbose=verbose, mock=mock, plan=plan, wave=wave,
        interactive=interactive, from_pr=from_pr, background=background,
        sandbox=sandbox, resume=resume,
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
        "stdio", "--transport", help="Transport type: stdio or sse",
    ),
    port: int = typer.Option(8080, "--port", help="Port for SSE transport"),
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


# === Plan sub-app ===

plan_app = typer.Typer(invoke_without_command=True)
app.add_typer(plan_app, name="plan", help="Manage plans")


@plan_app.callback()
def _plan_callback(ctx: typer.Context):
    """Manage plans."""
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@plan_app.command("list")
def plan_list():
    """List all plans."""
    args = SimpleNamespace(plan_action="list")
    return plan_command(args)


@plan_app.command("status")
def plan_status(name: str = typer.Argument(..., help="Plan name")):
    """Show plan progress."""
    args = SimpleNamespace(plan_action="status", name=name)
    return plan_command(args)


@plan_app.command("create")
def plan_create(
    description: str = typer.Argument(..., help="Intent/goal description for the plan"),
    plan_name: Optional[str] = typer.Option(
        None, "--name", "-n", help="Plan name (auto-generated if omitted)",
    ),
    protocol: str = typer.Option(
        ".snodo/protocol.yml", "--protocol", help="Path to protocol file",
    ),
    model: Optional[str] = typer.Option(
        None, "--model", "-m", help="Model to use",
    ),
    mock: bool = typer.Option(
        False, "--mock", help="Use mock coder instead of real LLM",
    ),
):
    """Create a new plan from an intent description."""
    args = SimpleNamespace(
        plan_action="create", description=description,
        plan_name=plan_name, protocol=protocol, model=model, mock=mock,
    )
    return plan_command(args)


# === Job sub-app ===

job_app = typer.Typer(invoke_without_command=True)
app.add_typer(job_app, name="job", help="Manage background jobs")


@job_app.callback()
def _job_callback(ctx: typer.Context):
    """Manage background jobs."""
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@job_app.command("list")
def job_list():
    """List all jobs."""
    args = SimpleNamespace(job_action="list")
    return job_command(args)


@job_app.command("status")
def job_status(job_id: str = typer.Argument(..., help="Job ID")):
    """Show job status."""
    args = SimpleNamespace(job_action="status", job_id=job_id)
    return job_command(args)


@job_app.command("logs")
def job_logs(
    job_id: str = typer.Argument(..., help="Job ID"),
    stream: str = typer.Option("stdout", "--stream", "-s", help="Log stream: stdout or stderr"),
    tail: Optional[int] = typer.Option(None, "--tail", "-n", help="Show last N lines"),
):
    """Show job logs."""
    args = SimpleNamespace(job_action="logs", job_id=job_id, stream=stream, tail=tail)
    return job_command(args)


@job_app.command("wait")
def job_wait(
    job_id: str = typer.Argument(..., help="Job ID"),
    timeout: Optional[float] = typer.Option(None, "--timeout", "-t", help="Max seconds to wait"),
):
    """Wait for job completion."""
    args = SimpleNamespace(job_action="wait", job_id=job_id, timeout=timeout)
    return job_command(args)


@job_app.command("cancel")
def job_cancel(job_id: str = typer.Argument(..., help="Job ID")):
    """Cancel a running job."""
    args = SimpleNamespace(job_action="cancel", job_id=job_id)
    return job_command(args)


# === Agent sub-app ===

agent_app = typer.Typer(invoke_without_command=True)
app.add_typer(agent_app, name="agent", help="Manage agent memory and threads")


@agent_app.callback()
def _agent_callback(ctx: typer.Context):
    """Manage agent memory and threads."""
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@agent_app.command("list")
def agent_list():
    """List all agents."""
    args = SimpleNamespace(agent_action="list")
    return agent_command(args)


@agent_app.command("memory")
def agent_memory(agent_id: str = typer.Argument(..., help="Agent ID (project:mode)")):
    """Show agent memory summary."""
    args = SimpleNamespace(agent_action="memory", agent_id=agent_id)
    return agent_command(args)


@agent_app.command("reset")
def agent_reset(agent_id: str = typer.Argument(..., help="Agent ID (project:mode)")):
    """Clear agent memory and assign new thread."""
    args = SimpleNamespace(agent_action="reset", agent_id=agent_id)
    return agent_command(args)


@agent_app.command("rotate")
def agent_rotate(agent_id: str = typer.Argument(..., help="Agent ID (project:mode)")):
    """Rotate agent thread ID (keeps old checkpoints)."""
    args = SimpleNamespace(agent_action="rotate", agent_id=agent_id)
    return agent_command(args)


# === Config sub-app ===

config_app = typer.Typer(invoke_without_command=True)
app.add_typer(config_app, name="config", help="Manage API keys and configuration")


@config_app.callback()
def _config_callback(ctx: typer.Context):
    """Manage API keys and configuration."""
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@config_app.command("show")
def config_show():
    """Show configured keys (masked)."""
    args = SimpleNamespace(config_action="show")
    return config_command(args)


@config_app.command("add")
def config_add(
    provider: str = typer.Argument(..., help="Provider name (openai, anthropic, google)"),
    key: str = typer.Argument(..., help="API key"),
):
    """Store an API key."""
    args = SimpleNamespace(config_action="add", provider=provider, key=key)
    return config_command(args)


@config_app.command("remove")
def config_remove(
    provider: str = typer.Argument(..., help="Provider name to remove"),
):
    """Remove an API key."""
    args = SimpleNamespace(config_action="remove", provider=provider)
    return config_command(args)


@config_app.command("test")
def config_test():
    """Validate all configured keys."""
    args = SimpleNamespace(config_action="test")
    return config_command(args)


@config_app.command("set")
def config_set_cmd(
    key: str = typer.Argument(..., help="Config key (e.g., engine.max_subtask_depth)"),
    value: str = typer.Argument(..., help="Value to set"),
):
    """Set a configuration value."""
    args = SimpleNamespace(config_action="set", key=key, value=value)
    return config_command(args)


@config_app.command("get")
def config_get_cmd(
    key: str = typer.Argument(..., help="Config key (e.g., engine.max_subtask_depth)"),
):
    """Get a configuration value."""
    args = SimpleNamespace(config_action="get", key=key)
    return config_command(args)


# === Session sub-app ===

session_app = typer.Typer(invoke_without_command=True)
app.add_typer(session_app, name="session", help="Manage protocol sessions")


@session_app.callback()
def _session_callback(ctx: typer.Context):
    """Manage protocol execution sessions."""
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@session_app.command("list")
def session_list(
    mode: Optional[str] = typer.Option(None, "--mode", help="Filter by mode"),
    project: Optional[str] = typer.Option(None, "--project", help="Filter by project path"),
    status: Optional[str] = typer.Option(None, "--status", help="Filter by status"),
):
    """List sessions."""
    args = SimpleNamespace(
        session_action="list", mode=mode, project=project, status=status,
    )
    return session_command(args)


@session_app.command("show")
def session_show(session_id: str = typer.Argument(..., help="Session ID")):
    """Show session details."""
    args = SimpleNamespace(session_action="show", session_id=session_id)
    return session_command(args)


@session_app.command("delete")
def session_delete(session_id: str = typer.Argument(..., help="Session ID")):
    """Delete a session."""
    args = SimpleNamespace(session_action="delete", session_id=session_id)
    return session_command(args)


@session_app.command("prune")
def session_prune():
    """Remove stale sessions."""
    args = SimpleNamespace(session_action="prune")
    return session_command(args)


# === Mode sub-app ===

mode_app = typer.Typer(invoke_without_command=True)
app.add_typer(mode_app, name="mode", help="Manage active protocol mode")


@mode_app.callback()
def _mode_callback(ctx: typer.Context):
    """Manage active protocol mode."""
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@mode_app.command("show")
def mode_show():
    """Show the current active mode."""
    args = SimpleNamespace(mode_action="show")
    return mode_command(args)


@mode_app.command("change")
def mode_change(
    new_mode: str = typer.Argument(..., help="Mode to switch to"),
):
    """Change the active protocol mode."""
    args = SimpleNamespace(mode_action="change", new_mode=new_mode)
    return mode_command(args)


# === Sandbox sub-app ===

sandbox_app = typer.Typer(invoke_without_command=True)
app.add_typer(sandbox_app, name="sandbox", help="Manage Docker sandbox")


@sandbox_app.callback()
def _sandbox_callback(ctx: typer.Context):
    """Manage Docker sandbox."""
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@sandbox_app.command("build")
def sandbox_build(
    tag: Optional[str] = typer.Option(
        None, "--tag", "-t", help="Image tag (default: snodo-worker:latest)",
    ),
):
    """Build the snodo-worker Docker image."""
    args = SimpleNamespace(sandbox_action="build", tag=tag)
    return sandbox_command(args)


@sandbox_app.command("status")
def sandbox_status():
    """Check Docker availability and image status."""
    args = SimpleNamespace(sandbox_action="status")
    return sandbox_command(args)


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


# === Resolve ===

@app.command()
def resolve(
    session_id: str = typer.Argument(..., help="Session ID"),
    task_id: str = typer.Argument(..., help="Task ID"),
    decision: str = typer.Option(
        ..., "--decision", "-d", help="Resolution: proceed or halt",
    ),
    justification: str = typer.Option(
        ..., "--justification", "-j", help="Justification for the decision",
    ),
    resolved_by: Optional[str] = typer.Option(
        None, "--resolved-by", help="Who resolved (default: cli)",
    ),
):
    """Resolve an escalated validator disagreement."""
    from snodo.cli.commands.resolve_cmd import resolve_command
    args = SimpleNamespace(
        session_id=session_id, task_id=task_id,
        decision=decision, justification=justification,
        resolved_by=resolved_by or "cli",
    )
    return resolve_command(args)


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
    except click.exceptions.ClickException:
        raise SystemExit(2)


if __name__ == "__main__":
    sys.exit(main())
