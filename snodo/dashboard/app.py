"""Snodo TUI Dashboard - Main application.

FILE: snodo/dashboard/app.py (Task 5.3)

Real-time observability dashboard built with Textual.
Shows active jobs, agents, plans, and audit events.
"""

from pathlib import Path
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.widgets import Header, Footer, Static

from snodo.dashboard.panels import JobsPanel, AgentsPanel, PlansPanel, EventsPanel


class SnodoDashboard(App):
    """Snodo real-time observability dashboard."""

    TITLE = "Snodo Dashboard"
    SUB_TITLE = ""

    CSS = """
    Screen {
        layout: vertical;
    }
    #workspace-bar {
        height: 1;
        background: $surface;
        padding: 0 1;
        color: $text-muted;
    }
    VerticalScroll {
        height: 1fr;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("c", "cancel_job", "Cancel Job"),
        Binding("j", "focus_next", "Down", show=False),
        Binding("k", "focus_previous", "Up", show=False),
    ]

    def __init__(self, project_root: Optional[str] = None, **kwargs):
        super().__init__(**kwargs)
        self.project_root = project_root or str(Path.cwd())
        self._refresh_timer = None

    def compose(self) -> ComposeResult:
        yield Header()
        project_name = Path(self.project_root).name
        yield Static(
            f"  Workspace: {self.project_root}  |  Project: {project_name}",
            id="workspace-bar",
        )
        with VerticalScroll():
            yield JobsPanel()
            yield AgentsPanel()
            yield PlansPanel()
            yield EventsPanel()
        yield Footer()

    def on_mount(self):
        """Start auto-refresh on mount."""
        self.action_refresh()
        self._refresh_timer = self.set_interval(1.0, self.action_refresh)

    def action_refresh(self):
        """Refresh all panels."""
        for panel in self.query("JobsPanel"):
            panel.refresh_data()
        for panel in self.query("AgentsPanel"):
            panel.refresh_data()
        for panel in self.query("PlansPanel"):
            panel.refresh_data()
        for panel in self.query("EventsPanel"):
            panel.refresh_data()

    def action_cancel_job(self):
        """Cancel the currently selected job."""
        jobs_panel = self.query_one(JobsPanel)
        job_id = jobs_panel.get_selected_job_id()
        if not job_id:
            self.notify("No job selected", severity="warning")
            return

        try:
            from snodo.jobs import JobManager
            mgr = JobManager(self.project_root)
            mgr.cancel(job_id)
            self.notify(f"Cancelled job {job_id}")
            self.action_refresh()
        except Exception as e:
            self.notify(f"Error: {e}", severity="error")


def run_dashboard(project_root: Optional[str] = None):
    """Run the Snodo TUI dashboard.

    Args:
        project_root: Project root directory (defaults to cwd)
    """
    app = SnodoDashboard(project_root=project_root)
    app.run()
