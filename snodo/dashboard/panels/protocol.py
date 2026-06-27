"""Protocol panel — read-only pipeline view.

FILE: snodo/dashboard/panels/protocol.py
"""

from typing import Any, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from snodo.dashboard.panels import register_panel


@register_panel("protocol")
class ProtocolScreen(Screen):
    """Read-only protocol pipeline view.

    Renders modes, tools, validators (pre/post execute), coder,
    disagreement policy, and global constraints from the loaded protocol.
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "pop_screen", "Back"),
    ]

    CSS = """
    ProtocolScreen {
        layout: vertical;
    }
    #protocol-header {
        height: auto;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        border-bottom: solid $primary;
    }
    #protocol-body {
        height: 1fr;
    }
    ProtocolScreen .section {
        height: auto;
        margin: 0 1;
        border: solid $surface-lighten-1;
    }
    ProtocolScreen .section-title {
        text-style: bold;
        background: $primary;
        color: $text;
        padding: 0 1;
        height: 1;
    }
    ProtocolScreen .section-content {
        padding: 0 1;
    }
    ProtocolScreen .label {
        color: $text-muted;
    }
    ProtocolScreen .value {
        color: $text;
    }
    ProtocolScreen DataTable {
        height: auto;
        max-height: 12;
    }
    """

    def __init__(self, provider: Any, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="protocol-header")
        with VerticalScroll(id="protocol-body"):
            yield Static("PROTOCOL PIPELINE", classes="section-title")
            yield Static(id="protocol-overview", classes="section-content")
            yield Static("MODES", classes="section-title")
            yield DataTable(id="protocol-modes", cursor_type="row")
            yield Static("PRE-EXECUTE VALIDATORS", classes="section-title")
            yield DataTable(id="protocol-pre-val", cursor_type="row")
            yield Static("POST-EXECUTE VALIDATORS", classes="section-title")
            yield DataTable(id="protocol-post-val", cursor_type="row")
            yield Static("DISAGREEMENT POLICY", classes="section-title")
            yield Static(id="protocol-policy", classes="section-content")
            yield Static("GLOBAL CONSTRAINTS", classes="section-title")
            yield DataTable(id="protocol-constraints", cursor_type="row")
        yield Footer()

    def on_mount(self):
        self._populate()

    def _populate(self):
        protocol = self.provider.get_protocol()
        error = self.provider.get_protocol_error()

        header = self.query_one("#protocol-header", Static)
        if error:
            header.update(
                f"  [bold]{self.provider.project_name}[/] > protocol  "
                f"|  [red]Error: {_escape(error)}[/]"
            )
            self.query_one("#protocol-overview", Static).update(
                "[red]Protocol could not be loaded.[/]\n"
                "Check that .snodo/protocol.yml is valid YAML and passes all"
                " well-formedness checks."
            )
            for table_id in ("protocol-modes", "protocol-pre-val",
                             "protocol-post-val", "protocol-constraints"):
                self.query_one(f"#{table_id}", DataTable).visible = False
            self.query_one("#protocol-policy", Static).update("—")
            return

        if protocol is None:
            header.update(
                f"  [bold]{self.provider.project_name}[/] > protocol  "
                f"|  [yellow]No protocol loaded[/]"
            )
            self.query_one("#protocol-overview", Static).update(
                "[yellow]No protocol.yml found in .snodo/[/]"
            )
            return

        header.update(
            f"  [bold]{self.provider.project_name}[/] > protocol  "
            f"|  [bold]{protocol.protocol_id}[/] v{protocol.version}  "
            f"|  {protocol.name}"
        )

        # Overview
        exec_cfg = protocol.execution
        overview = (
            f"  Initial mode: [bold]{protocol.initial_mode}[/]\n"
            f"  Max retries: {exec_cfg.max_retries}  "
            f"|  Branch TTL: {exec_cfg.branch_ttl_days}d  "
            f"|  Branch prefix: {exec_cfg.branch_prefix}\n"
            f"  Modes: {len(protocol.modes)}  "
            f"|  Validators: {len(protocol.validators)}  "
            f"|  Global constraints: {len(protocol.global_constraints)}"
        )
        self.query_one("#protocol-overview", Static).update(overview)

        # Modes table
        mt = self.query_one("#protocol-modes", DataTable)
        mt.add_columns("Mode", "Tools", "Validator IDs", "Coder")
        for m in protocol.modes:
            tools = ", ".join(m.tools) if m.tools else "—"
            validators = ", ".join(m.validators) if m.validators else "—"
            coder = m.coder or "—"
            transitions = ", ".join(f"{k}→{v}" for k, v in m.transitions.items())
            label = f"{m.mode_id}  ({m.name})"
            if transitions:
                label += f"  [{transitions}]"
            mt.add_row(label, tools, validators, coder)

        # Pre-execute validators
        pre_val = protocol.get_validators_by_phase("pre_execute")
        pret = self.query_one("#protocol-pre-val", DataTable)
        pret.add_columns("Validator", "Type", "Severity Cap", "Model")
        for v in pre_val:
            cap = v.severity_cap.value if v.severity_cap else "—"
            model = v.model or "—"
            pret.add_row(v.validator_id, v.validator_type, cap, model)

        # Post-execute validators
        post_val = protocol.get_validators_by_phase("post_execute")
        postt = self.query_one("#protocol-post-val", DataTable)
        postt.add_columns("Validator", "Type", "Severity Cap", "Model")
        for v in post_val:
            cap = v.severity_cap.value if v.severity_cap else "—"
            model = v.model or "—"
            postt.add_row(v.validator_id, v.validator_type, cap, model)

        if not post_val:
            postt.add_row("—", "—", "—", "—")

        # Disagreement policy
        policy = protocol.disagreement_policy
        policy_desc = self._policy_description(policy)
        self.query_one("#protocol-policy", Static).update(policy_desc)

        # Global constraints
        ct = self.query_one("#protocol-constraints", DataTable)
        ct.add_columns("Constraint", "Description", "Severity")
        for c in protocol.global_constraints:
            ct.add_row(c.constraint_id, c.description, c.severity.value)

        if not protocol.global_constraints:
            ct.add_row("—", "None configured", "—")

    @staticmethod
    def _policy_description(policy: Any) -> str:
        mapping = {
            "unanimous": "All validators must pass before proceeding.",
            "majority": "More than 50% of validators must pass.",
            "quorum": "Configurable threshold of validators must pass.",
            "any": "At least one validator must pass.",
        }
        base = mapping.get(policy.value if hasattr(policy, "value") else str(policy),
                           f"Policy: {policy}")
        return (
            f"  [bold]{policy.value if hasattr(policy, 'value') else policy}[/]\n"
            f"  {base}\n"
            f"  On split: → human review  |  Blocker: → stop"
        )


def _escape(text: str) -> str:
    """Minimal escape for Rich markup."""
    return text.replace("[", "\\[").replace("]", "\\]")
