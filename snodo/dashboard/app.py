"""Snodo TUI Dashboard — k9s-style session monitor.

FILE: snodo/dashboard/app.py (Task: dashboard rebuild)

Keyboard-first, live TUI dashboard. Sessions are the spine.
Live in-place updates — never table.clear()+re-add.
"""

from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header

from snodo.dashboard.providers import DashboardDataProvider
from snodo.dashboard.screens import SessionsScreen


def _snodo_version() -> str:
    try:
        from snodo import __version__
        return __version__
    except Exception:
        return "unknown"


class SnodoDashboard(App):
    """Snodo k9s-style session-monitor dashboard."""

    TITLE = f"Snodo Dashboard  v{_snodo_version()}"
    SUB_TITLE = ""

    CSS = """
    Screen {
        layout: vertical;
    }
    DataTable {
        height: 1fr;
    }
    DataTable > .datatable--header {
        text-style: bold;
        background: $panel;
    }
    DataTable > .datatable--cursor {
        background: $surface;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, project_root: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.project_root = project_root or str(Path.cwd())
        self.provider = DashboardDataProvider(self.project_root)

    def compose(self) -> ComposeResult:
        yield Header()
        yield Footer()

    def on_mount(self):
        self.push_screen(SessionsScreen(self.provider))


def run_dashboard(project_root: Optional[str] = None):
    """Run the Snodo TUI dashboard.

    Args:
        project_root: Project root directory (defaults to cwd)
    """
    app = SnodoDashboard(project_root=project_root)
    app.run()
