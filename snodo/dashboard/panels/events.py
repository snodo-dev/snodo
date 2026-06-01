"""Events panel for the TUI dashboard.

FILE: snodo/dashboard/panels/events.py
"""

from pathlib import Path

from textual.widgets import Static
from textual.containers import Vertical


class EventsPanel(Vertical):
    """Panel showing recent audit events."""

    DEFAULT_CSS = """
    EventsPanel {
        height: auto;
        max-height: 10;
        border: solid $warning;
        padding: 0 1;
    }
    EventsPanel .panel-title {
        text-style: bold;
        color: $text;
    }
    """

    def compose(self):
        yield Static("RECENT EVENTS", classes="panel-title")
        yield Static("", id="events-content")

    def refresh_data(self):
        """Refresh events from .snodo/audit.log."""
        content = self.query_one("#events-content", Static)
        events = self._load_events()

        if not events:
            content.update("  (no events)")
            return

        lines = []
        for ev in events[-8:]:  # Show last 8
            ts = ev.timestamp
            # Extract time portion (HH:MM)
            time_part = ts[11:16] if len(ts) > 16 else ts[:5]
            event_type = ev.event_type
            data_summary = self._summarize_data(ev.data)
            lines.append(f"  {time_part} {event_type:<20} {data_summary}")
        content.update("\n".join(lines))

    def _load_events(self):
        """Load audit events from .snodo/audit.log."""
        try:
            from snodo.infrastructure.audit import AuditLog
            log_path = str(Path.cwd() / ".snodo" / "audit.log")
            if not Path(log_path).exists():
                return []
            audit = AuditLog(log_path)
            return audit.get_history()
        except Exception:
            return []

    def _summarize_data(self, data):
        """Create a short summary of event data."""
        if not data:
            return ""
        # Show first key-value pair
        for k, v in data.items():
            val = str(v)
            if len(val) > 30:
                val = val[:27] + "..."
            return f"{k}={val}"
        return ""
