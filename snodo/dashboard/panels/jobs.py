"""Jobs panel for the TUI dashboard.

FILE: snodo/dashboard/panels/jobs.py
"""

import time
from pathlib import Path

from textual.widgets import Static, DataTable
from textual.containers import Vertical


class JobsPanel(Vertical):
    """Panel showing active and recent jobs."""

    DEFAULT_CSS = """
    JobsPanel {
        height: auto;
        max-height: 12;
        border: solid $primary;
        padding: 0 1;
    }
    JobsPanel .panel-title {
        text-style: bold;
        color: $text;
    }
    """

    def compose(self):
        yield Static("JOBS", classes="panel-title")
        table = DataTable(id="jobs-table")
        table.cursor_type = "row"
        yield table

    def on_mount(self):
        table = self.query_one("#jobs-table", DataTable)
        table.add_columns("ID", "Status", "Description", "Age")

    def refresh_data(self):
        """Refresh job data from .snodo/jobs/."""
        table = self.query_one("#jobs-table", DataTable)
        table.clear()

        jobs = self._load_jobs()
        for job in jobs[:10]:  # Show at most 10
            desc = job["description"]
            if len(desc) > 35:
                desc = desc[:32] + "..."
            age = self._format_age(job.get("created_at", 0))
            table.add_row(job["id"], job["status"], desc, age)

    def _load_jobs(self):
        """Load jobs from .snodo/jobs/ directory."""
        try:
            from snodo.jobs import JobManager
            project_root = str(Path.cwd())
            mgr = JobManager(project_root)
            return mgr.list_jobs()
        except (ValueError, Exception):
            return []

    def _format_age(self, created_at):
        """Format creation time as relative age."""
        if not created_at:
            return "N/A"
        elapsed = time.time() - created_at
        if elapsed < 60:
            return f"{int(elapsed)}s"
        elif elapsed < 3600:
            return f"{int(elapsed / 60)}m"
        elif elapsed < 86400:
            return f"{int(elapsed / 3600)}h"
        return f"{int(elapsed / 86400)}d"

    def get_selected_job_id(self):
        """Get the currently selected job ID from the table."""
        table = self.query_one("#jobs-table", DataTable)
        if table.cursor_row is not None:
            try:
                row = table.get_row_at(table.cursor_row)
                return row[0] if row else None
            except Exception:
                return None
        return None
