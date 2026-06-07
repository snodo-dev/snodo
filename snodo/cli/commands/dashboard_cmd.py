"""Dashboard command - Launch TUI dashboard.

FILE: snodo/cli/commands/dashboard_cmd.py (Task 5.3)
"""

import sys
from pathlib import Path

from snodo.infrastructure.paths import resolve_project_root


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
