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
from typing import Optional

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


# === Auto-discovery: mount command modules that expose `app` (group) or `register` (command) ===
# Any snodo/cli/commands/*_cmd.py that defines:
#   • app = typer.Typer(...)  →  mounted as a sub-app (groups)
#   • register(app)           →  called to attach @app.command() decorators (top-level commands)

import pkgutil as _pkgutil
import importlib as _importlib
import snodo.cli.commands as _cli_commands

for _, _mod_name, _ in _pkgutil.iter_modules(_cli_commands.__path__):
    _mod = _importlib.import_module(f"{_cli_commands.__name__}.{_mod_name}")
    _sub_app = getattr(_mod, "app", None)
    if isinstance(_sub_app, typer.Typer):
        _cmd_name = getattr(_mod, "COMMAND_NAME", _mod_name.replace("_cmd", ""))
        app.add_typer(_sub_app, name=_cmd_name)
    _reg = getattr(_mod, "register", None)
    if callable(_reg):
        _reg(app)

del _pkgutil, _importlib, _cli_commands, _mod_name, _mod, _sub_app, _cmd_name, _reg


# init is now registered in snodo/cli/commands/init_cmd.py via register(app).


# run is now registered in snodo/cli/commands/run_cmd.py via register(app).


# serve is now registered in snodo/cli/commands/serve_cmd.py via register(app).


# dashboard is now registered in snodo/cli/commands/dashboard_cmd.py via register(app).


# plan sub-app is now defined in snodo/cli/commands/plan_cmd.py
# and mounted automatically by the discovery loop above.


# logs is now registered in snodo/cli/commands/logs_cmd.py via register(app).


# meta is now registered in snodo/cli/commands/meta_cmd.py via register(app).


# models is now registered in snodo/cli/commands/models_cmd.py via register(app).


# recon is now registered in snodo/cli/commands/recon_cmd.py via register(app).


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


# install + uninstall are now registered in snodo/cli/commands/install_cmd.py via register(app).


# authorize is now registered in snodo/cli/commands/authorize_cmd.py via register(app).


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
