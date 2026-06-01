"""Agents panel for the TUI dashboard.

FILE: snodo/dashboard/panels/agents.py
"""

from textual.widgets import Static, DataTable
from textual.containers import Vertical


class AgentsPanel(Vertical):
    """Panel showing registered agents and their status."""

    DEFAULT_CSS = """
    AgentsPanel {
        height: auto;
        max-height: 10;
        border: solid $secondary;
        padding: 0 1;
    }
    AgentsPanel .panel-title {
        text-style: bold;
        color: $text;
    }
    """

    def compose(self):
        yield Static("AGENTS", classes="panel-title")
        table = DataTable(id="agents-table")
        table.cursor_type = "row"
        yield table

    def on_mount(self):
        table = self.query_one("#agents-table", DataTable)
        table.add_columns("ID", "Thread", "Tasks", "Last Used")

    def refresh_data(self):
        """Refresh agent data from ~/.snodo/agents.json."""
        table = self.query_one("#agents-table", DataTable)
        table.clear()

        agents = self._load_agents()
        for agent in agents[:8]:
            thread_short = agent.get("thread_id", "")[:8]
            tasks = str(agent.get("task_count", 0))
            last = self._format_last_used(agent.get("last_used_at"))
            table.add_row(agent["id"], thread_short, tasks, last)

    def _load_agents(self):
        """Load agents from memory manager."""
        try:
            from snodo.infrastructure.memory import AgentMemoryManager
            mgr = AgentMemoryManager()
            return mgr.list_agents()
        except Exception:
            return []

    def _format_last_used(self, ts):
        """Format timestamp as relative time."""
        if not ts:
            return "never"
        import time
        elapsed = time.time() - ts
        if elapsed < 60:
            return "just now"
        elif elapsed < 3600:
            return f"{int(elapsed / 60)}m ago"
        elif elapsed < 86400:
            return f"{int(elapsed / 3600)}h ago"
        return f"{int(elapsed / 86400)}d ago"
