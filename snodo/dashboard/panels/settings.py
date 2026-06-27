"""Settings panel — read-only global config view.

FILE: snodo/dashboard/panels/settings.py
"""

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from snodo.dashboard.panels import register_panel


@register_panel("settings")
class SettingsScreen(Screen):
    """Read-only global configuration view.

    Renders default model, coder/validator models from protocol,
    recovery budget (max retries / max subtask depth), and
    configured providers from ConfigManager.
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "pop_screen", "Back"),
    ]

    CSS = """
    SettingsScreen {
        layout: vertical;
    }
    #settings-header {
        height: auto;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        border-bottom: solid $primary;
    }
    #settings-body {
        height: 1fr;
    }
    SettingsScreen .section {
        height: auto;
        margin: 0 1;
        border: solid $surface-lighten-1;
    }
    SettingsScreen .section-title {
        text-style: bold;
        background: $primary;
        color: $text;
        padding: 0 1;
        height: 1;
    }
    SettingsScreen .section-content {
        padding: 0 1;
    }
    SettingsScreen DataTable {
        height: auto;
        max-height: 10;
    }
    SettingsScreen .setting-row {
        height: 1;
    }
    SettingsScreen .label {
        color: $text-muted;
    }
    SettingsScreen .value {
        color: $text;
    }
    """

    def __init__(self, provider: Any, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="settings-header")
        with VerticalScroll(id="settings-body"):
            yield Static("GLOBAL CONFIGURATION", classes="section-title")
            yield Static(id="settings-overview", classes="section-content")
            yield Static("PROTOCOL MODELS", classes="section-title")
            yield DataTable(id="settings-protocol-models", cursor_type="row")
            yield Static("RECOVERY BUDGET", classes="section-title")
            yield Static(id="settings-recovery", classes="section-content")
            yield Static("PROVIDERS", classes="section-title")
            yield DataTable(id="settings-providers", cursor_type="row")
        yield Footer()

    def on_mount(self):
        self._populate()

    def _populate(self):
        from snodo.config import ConfigManager

        config = ConfigManager()
        protocol = self.provider.get_protocol()

        header = self.query_one("#settings-header", Static)
        header.update(
            f"  [bold]{self.provider.project_name}[/] > settings  "
            f"|  Read-only view"
        )

        # Default model
        default_model = config.get_model()
        self.query_one("#settings-overview", Static).update(
            f"  Default model: [bold]{default_model}[/]\n"
            f"  Config file: {config.config_path}"
        )

        # Protocol models — coder / validator model per mode
        pmt = self.query_one("#settings-protocol-models", DataTable)
        pmt.add_columns("Mode", "Coder", "Coder Config", "Validators")
        if protocol:
            for m in protocol.modes:
                coder = m.coder or "(default)"
                coder_cfg = "; ".join(
                    f"{k}={v}" for k, v in m.coder_config.items()
                ) if m.coder_config else "—"
                # Collect validator model overrides
                v_models = []
                for vid in m.validators:
                    v = protocol.get_validator(vid)
                    if v and v.model:
                        v_models.append(f"{vid}:{v.model}")
                val_str = ", ".join(v_models) if v_models else "(default)"
                pmt.add_row(m.mode_id, coder, coder_cfg, val_str)
        else:
            pmt.add_row("—", "(default)", "—", "(default)")

        # Recovery budget
        max_retries = protocol.execution.max_retries if protocol else 3
        max_depth = config.get_engine_value("max_subtask_depth", 3)
        max_age = config.get_engine_value("max_session_age_days", 30)
        self.query_one("#settings-recovery", Static).update(
            f"  Max retries per task: [bold]{max_retries}[/]\n"
            f"  Max subtask depth: [bold]{max_depth}[/]\n"
            f"  Max session age: [bold]{max_age}d[/]"
        )

        # Providers
        pt = self.query_one("#settings-providers", DataTable)
        pt.add_columns("Provider", "Default Model", "Key Set")
        for pname, pcfg in config.get_providers().items():
            has_key = bool(config.get_key(pname))
            key_status = "[green]✓[/]" if has_key else "[yellow]—[/]"
            model = getattr(pcfg, "default_model", "") or "—"
            pt.add_row(pname, str(model)[:40], key_status)
