"""Dashboard command - Launch the terminal or local web dashboard."""

import sys
from pathlib import Path
from types import SimpleNamespace

import typer

from snodo.infrastructure.paths import resolve_project_root


def register(app: typer.Typer) -> None:
    """Register top-level commands onto the CLI app."""

    @app.command()
    def dashboard(
        web: bool = typer.Option(False, "--web/--cli", help="Launch the local web dashboard"),
        port: int | None = typer.Option(None, "--port", help="Web dashboard port"),
        root: list[Path] | None = typer.Option(None, "--root", help="Project scan root (repeatable)"),
    ) -> None:
        """Launch the terminal dashboard, or the local web dashboard with --web."""
        if web:
            from snodo.web.projects import resolve_projects
            from snodo.web.roots import load_roots, save_roots
            from snodo.web.app import serve

            roots = root if root else load_roots()
            if root:
                save_roots(root)
            projects = resolve_projects(roots, Path.cwd())
            if not projects:
                typer.echo(
                    "Error: No Snodo projects found; pass --root or run this command inside a snodo project.",
                    err=True,
                )
                raise typer.Exit(1)
            serve(projects, port)
            return

        if port is not None or root:
            typer.echo("Error: --port and --root are only valid with --web.", err=True)
            raise typer.Exit(1)
        return dashboard_command(SimpleNamespace())


def snop_entry():
    """Entry point for the 'snop' shortcut command."""
    from types import SimpleNamespace
    args = SimpleNamespace()
    sys.exit(dashboard_command(args))


def dashboard_command(args) -> int:
    """Launch the Snodo TUI dashboard."""
    project_root = resolve_project_root() or str(Path.cwd())

    # Verify .snodo/ exists
    snodo_dir = Path(project_root) / ".snodo"
    if not snodo_dir.is_dir():
        print("Error: Not a snodo project (no .snodo/ directory)", file=sys.stderr)
        print("Run 'snodo init' first.", file=sys.stderr)
        return 1

    try:
        from snodo.dashboard.app import run_dashboard
        run_dashboard(project_root=project_root)
        return 0
    except ImportError as e:
        print(f"Error: Dashboard requires 'textual' package: {e}", file=sys.stderr)
        print("Install with: pip install textual", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: Dashboard failed: {e}", file=sys.stderr)
        return 1
