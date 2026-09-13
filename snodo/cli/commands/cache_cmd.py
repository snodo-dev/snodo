"""Cache command group — inspect and clear the project's verdict cache.

FILE: snodo/cli/commands/cache_cmd.py

The verdict cache (#246) is an optimisation, never a source of truth: it
stores a validator's verdict against the exact question it answered so a
recovery attempt does not re-buy a judgement whose inputs did not move.  The
first thing an operator does when they suspect a cache is delete it, so that
has a first-class command rather than a guessed path.

``snodo cache clear`` removes the project's cache file.  It cannot affect the
audit log or any verdict already recorded there — the cache is not part of
the audit trail — and the next run simply judges fresh.
"""

import sys
from pathlib import Path

import typer

COMMAND_NAME = "cache"

app = typer.Typer(
    invoke_without_command=True,
    help="Inspect or clear the project's verdict cache",
)


@app.callback()
def _cache_callback(ctx: typer.Context):
    """Inspect or clear the project's verdict cache."""
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


def _resolve_cache_path() -> Path | None:
    from snodo.infrastructure.paths import resolve_project_root
    from snodo.validators.verdict_cache import default_cache_path

    project_root = resolve_project_root()
    if project_root is None:
        return None
    return default_cache_path(project_root)


@app.command("clear")
def cache_clear(
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Delete the verdict cache; the next run judges fresh."""
    from snodo.cli.json_output import (
        EXIT_INTERNAL_ERROR,
        EXIT_PASS,
        emit_error,
        emit_json,
        schema_name,
    )

    path = _resolve_cache_path()
    if path is None:
        if json:
            return emit_error(
                "cache_clear", "Not inside a snodo project.", EXIT_INTERNAL_ERROR
            )
        print("Not inside a snodo project.", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    existed = path.exists()
    try:
        path.unlink()
    except FileNotFoundError:
        existed = False
    except OSError as e:
        if json:
            return emit_error(
                "cache_clear", f"Could not remove {path}: {e}", EXIT_INTERNAL_ERROR
            )
        print(f"Error: could not remove verdict cache {path}: {e}", file=sys.stderr)
        return EXIT_INTERNAL_ERROR

    if json:
        return emit_json(
            {
                "schema": schema_name("cache_clear"),
                "ok": True,
                "cleared": existed,
                "path": str(path),
            },
            EXIT_PASS,
        )

    if existed:
        print(f"Cleared verdict cache: {path}")
    else:
        print(f"No verdict cache to clear (nothing at {path}).")
    return EXIT_PASS
