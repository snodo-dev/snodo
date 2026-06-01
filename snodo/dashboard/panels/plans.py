"""Plans panel for the TUI dashboard.

FILE: snodo/dashboard/panels/plans.py
"""

from pathlib import Path

from textual.widgets import Static
from textual.containers import Vertical


class PlansPanel(Vertical):
    """Panel showing active plan progress."""

    DEFAULT_CSS = """
    PlansPanel {
        height: auto;
        max-height: 10;
        border: solid $accent;
        padding: 0 1;
    }
    PlansPanel .panel-title {
        text-style: bold;
        color: $text;
    }
    PlansPanel .plan-name {
        color: $text;
    }
    PlansPanel .plan-progress {
        color: $text-muted;
    }
    """

    def compose(self):
        yield Static("PLANS", classes="panel-title")
        yield Static("", id="plans-content")

    def refresh_data(self):
        """Refresh plan data from .snodo/plans/."""
        content = self.query_one("#plans-content", Static)
        plans = self._load_plans()

        if not plans:
            content.update("  (no plans)")
            return

        lines = []
        for p in plans[:5]:
            name = p.get("name", "unnamed")
            counts = p.get("status_counts", {})
            done = counts.get("completed", 0)
            total = p.get("task_count", 0)
            pct = int((done / total * 100) if total > 0 else 0)
            bar = self._progress_bar(pct)
            lines.append(f"  {name}: {bar} {pct}% ({done}/{total})")
        content.update("\n".join(lines))

    def _load_plans(self):
        """Load plans from .snodo/plans/."""
        try:
            from snodo.mcp.planner import PlannerMCP
            project_root = str(Path.cwd())
            planner = PlannerMCP(project_root)
            return planner.list_plans()
        except (ValueError, Exception):
            return []

    def _progress_bar(self, pct):
        """Create a text-based progress bar."""
        filled = int(pct / 10)
        empty = 10 - filled
        return "[" + "#" * filled + "-" * empty + "]"
