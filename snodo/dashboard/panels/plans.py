"""Plans panel — what an orchestrator proposed, wave by wave.

FILE: snodo/dashboard/panels/plans.py

An orchestrator's plan arrives as tasks that share a plan and split across
waves; on the cockpit each one reads as an unrelated job row, and the
connection has to be inferred from task names. The information is already on
disk — plan.yml says which waves exist, what each depends on, and which tasks
belong to which; status.json says where every task stands — so this pane is a
view, nothing more. It renders each plan's waves, each wave's tasks under it,
the status of each task as status.json records it, and, for a task that is
running now, the job carrying it: named here, readable in the cockpit's Jobs
pane one step away.

The dashboard is an observer. This panel offers no run, no retry, no cancel —
it has no key that changes state. Rows are coloured by status (the shared
mapping in screens.py), and the status word stays in its column, so colour is
never the only carrier of meaning.
"""

from contextlib import suppress
from typing import Any, Dict, List, Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Header, Static
from textual.widgets.data_table import RowDoesNotExist

from snodo.dashboard.panels import register_panel
from snodo.dashboard.screens import StatusRowTable


@register_panel("plans")
class PlansScreen(Screen):
    """Read-only view of .snodo/plans/: waves, dependencies, task states."""

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("escape", "pop_screen", "Back"),
    ]

    CSS = """
    PlansScreen {
        layout: vertical;
    }
    #plans-header {
        height: auto;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
        border-bottom: solid $primary;
    }
    #plans-table {
        height: 1fr;
    }
    """

    def __init__(self, provider: Any, **kwargs):
        super().__init__(**kwargs)
        self.provider = provider
        self._selected_key: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="plans-header")
        yield StatusRowTable(id="plans-table", cursor_type="row")
        yield Footer()

    def on_mount(self):
        table = self.query_one("#plans-table", StatusRowTable)
        table.add_columns("Plan · Wave · Task", "Status", "Job", "Detail")
        self._populate()

    def on_screen_resume(self):
        self._populate()

    def action_refresh(self):
        self._populate()

    def _populate(self) -> None:
        """Rebuild the table from one fresh read; keep the cursor's row.

        The whole table is cleared and re-added on every pass, so selection is
        restored by row key afterwards — the same discipline the cockpit uses
        (#239): a move made by the rebuild must not be mistaken for the
        operator moving, and a row that vanished must not raise.
        """
        table = self.query_one("#plans-table", StatusRowTable)
        if table.row_count:
            self._remember_cursor(table)

        table.clear()
        plans = self.provider.get_plans()
        carriers = self._job_carriers()

        if not plans:
            table.add_row("[dim]No plans on disk.[/]", "—", "—", "—", key="plans:empty")
            self._update_header(0)
            return

        for plan in plans:
            self._add_plan_rows(table, plan, carriers)

        if self._selected_key is not None:
            with suppress(RowDoesNotExist):
                table.move_cursor(row=table.get_row_index(self._selected_key))

        self._update_header(len(plans))

    def _remember_cursor(self, table: StatusRowTable) -> None:
        key = table.key_at_row(table.cursor_row)
        if key is not None:
            self._selected_key = str(getattr(key, "value", key))

    def _job_carriers(self) -> Dict[str, str]:
        """task_id → the job carrying it, for tasks that are running now.

        The latest started non-terminal job per task; a settled task names no
        carrier here — the cockpit's Jobs pane lists every job with its task,
        which is where finished work is read.
        """
        carriers: Dict[str, Dict[str, Any]] = {}
        for j in self.provider.get_jobs(""):
            task_id = j.get("task_ref") or ""
            if not task_id or j.get("status") not in ("queued", "running"):
                continue
            best = carriers.get(task_id)
            started = j.get("started_at") or j.get("created_at") or 0.0
            if best is None or started >= (best.get("started_at") or 0.0):
                carriers[task_id] = j
        return {tid: j["job_id"] for tid, j in carriers.items()}

    def _add_plan_rows(
        self,
        table: StatusRowTable,
        plan: Dict[str, Any],
        carriers: Dict[str, str],
    ) -> None:
        name = plan["name"]
        tasks: Dict[str, str] = plan.get("tasks") or {}
        waves: List[Dict[str, Any]] = plan.get("waves") or []

        done = sum(1 for s in tasks.values() if s in ("completed", "merged"))
        summary = f"{done}/{len(tasks)} done" if tasks else "no tasks yet"
        table.add_row(
            f"[bold]{name}[/]", summary, "—",
            f"[dim]{plan.get('intent') or '—'}[/]",
            key=f"plan:{name}",
        )

        planned = set()
        for w in waves:
            wave_tasks = [t for t in w.get("tasks") or []]
            planned.update(wave_tasks)
            deps = w.get("depends_on") or []
            dep_str = "after: " + ", ".join(str(d) for d in deps) if deps else "no dependencies"
            table.add_row(f"  Wave {w.get('id')}", "—", "—", f"[dim]{dep_str}[/]",
                          key=f"plan:{name}:wave:{w.get('id')}")
            for tid in wave_tasks:
                self._add_task_row(table, name, tid, tasks, carriers)

        for tid in tasks:
            if tid not in planned:
                self._add_task_row(table, name, tid, tasks, carriers)

    def _add_task_row(
        self,
        table: StatusRowTable,
        plan_name: str,
        task_id: str,
        tasks: Dict[str, str],
        carriers: Dict[str, str],
    ) -> None:
        status = tasks.get(task_id, "pending")
        carrier = carriers.get(task_id, "—")
        table.add_row(
            f"    ↳ {task_id}", status, carrier, "",
            key=f"plan:{plan_name}:task:{task_id}",
            status=status,
        )

    def _update_header(self, plan_count: int) -> None:
        header = self.query_one("#plans-header", Static)
        header.update(
            f"  [bold]{self.provider.project_name}[/] > plans  "
            f"|  {plan_count} plan(s)  "
            f"|  [dim]read-only: this pane changes nothing[/]"
        )
        self.app.sub_title = "  r:refresh  esc:back  q:quit"
