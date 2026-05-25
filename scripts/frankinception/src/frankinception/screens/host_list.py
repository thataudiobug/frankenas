"""Top-level screen: list hosts and offer global actions."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Static

from ..state import AppState


class HostListScreen(Screen):
    """Pick a host to edit, or open one of the global tools."""

    BINDINGS = [
        Binding("c", "import_compose", "Import compose"),
        Binding("p", "run_play", "Run playbook"),
        Binding("r", "reload", "Reload"),
        Binding("q", "request_quit", "Quit"),
    ]

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(self._banner(), id="banner")
            with Horizontal(id="body"):
                with Vertical(id="hosts-pane", classes="pane"):
                    yield DataTable(id="hosts", cursor_type="row", zebra_stripes=True)
                with Vertical(id="actions-pane", classes="pane"):
                    yield Static(self._actions_text(), id="actions")
        yield Footer()

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#hosts", DataTable)
        table.add_columns("Host", "Direct groups", "Inherited")
        self._refill_table(table)
        table.focus()

    # ---- actions -----------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # DataTable fires this on Enter (and on click). We use it instead of a
        # screen-level binding so the cursor row is always reliable.
        if event.row_key.value is None:
            return
        from .host_editor import HostEditorScreen

        self.app.push_screen(HostEditorScreen(self.state, str(event.row_key.value)))

    def action_import_compose(self) -> None:
        from .compose_import import ComposeImportScreen

        self.app.push_screen(ComposeImportScreen(self.state))

    def action_run_play(self) -> None:
        from .play_runner import PlayRunnerScreen

        # The play runner now prompts for limit scope after a play is picked,
        # so we don't pre-fill anything from the cursor row.
        self.app.push_screen(PlayRunnerScreen(self.state))

    def action_reload(self) -> None:
        self.state = AppState.load(self.state.layout)
        self._refill_table(self.query_one("#hosts", DataTable))
        self.notify("Reloaded inventory from disk", timeout=2)

    def action_request_quit(self) -> None:
        self.app.exit()

    # ---- helpers -----------------------------------------------------

    def _banner(self) -> str:
        return (
            f"[b]frankinception[/b]   "
            f"inventory: [cyan]{self.state.layout.inventory_dir}[/cyan]\n"
            f"hosts.yml: {self.state.layout.hosts_file}"
        )

    def _actions_text(self) -> str:
        return (
            "[b]Actions[/b]\n\n"
            "Enter — edit host\n"
            "c — import docker-compose / docker run\n"
            "p — run a playbook\n"
            "r — reload from disk\n"
            "q — quit"
        )

    def _refill_table(self, table: DataTable) -> None:
        table.clear()
        inv = self.state.inventory
        for host in inv.hosts():
            direct = inv.direct_groups_of(host)
            inherited = [g for g in inv.all_groups_of(host) if g not in direct]
            table.add_row(
                host,
                ", ".join(direct) or "—",
                ", ".join(inherited) or "—",
                key=host,
            )
