"""Pick a playbook, optionally limit it to a host, and run it."""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
)

from .. import runner
from ..plays import Playbook, list_playbooks
from ..state import AppState


class PlayRunnerScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("enter", "run", "Run"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, state: AppState, default_limit: str | None = None) -> None:
        super().__init__()
        self.state = state
        self.default_limit = default_limit or ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(
                f"[b]Run playbook[/b]   plays dir: [cyan]{self.state.layout.plays_dir}[/cyan]\n"
                "Enter on a row to run with current options.",
                id="play-heading",
            )
            yield DataTable(id="plays", cursor_type="row", zebra_stripes=True)
            with Horizontal(id="play-options"):
                yield Label("Limit:")
                yield Input(value=self.default_limit, placeholder="host or pattern", id="limit-input")
                yield Checkbox("Check mode (--check)", id="check-cb")
                yield Button("Run", id="run-btn", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#plays", DataTable)
        table.add_columns("Playbook", "Description")
        self._refill(table)
        table.focus()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self._refill(self.query_one("#plays", DataTable))

    def action_run(self) -> None:
        self._run_selected()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-btn":
            self._run_selected()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is not None:
            self._run(str(event.row_key.value))

    def _run_selected(self) -> None:
        table: DataTable = self.query_one("#plays", DataTable)
        if table.row_count == 0:
            return
        try:
            row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
        except Exception:
            return
        if row_key.value is None:
            return
        self._run(str(row_key.value))

    def _run(self, name: str) -> None:
        play = next((p for p in self._plays if p.name == name), None)
        if play is None:
            return
        limit = self.query_one("#limit-input", Input).value.strip() or None
        check = self.query_one("#check-cb", Checkbox).value
        invocation = runner.build(
            playbook=play.path,
            project_root=self.state.layout.project_root,
            inventory_dir=self.state.layout.inventory_dir,
            limit=limit,
            check=check,
        )
        self.app.push_screen(_RunOutputScreen(invocation, play))

    # ---- helpers -----------------------------------------------------

    def _refill(self, table: DataTable) -> None:
        table.clear()
        self._plays: list[Playbook] = list_playbooks(self.state.layout.plays_dir)
        for p in self._plays:
            table.add_row(p.name, p.description, key=p.name)


class _RunOutputScreen(ModalScreen[None]):
    """Stream subprocess output live."""

    BINDINGS = [
        Binding("escape", "back", "Close"),
    ]

    def __init__(self, invocation: runner.Invocation, play: Playbook) -> None:
        super().__init__()
        self.invocation = invocation
        self.play = play
        self._proc: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(
                f"[b]{self.play.name}[/b]\n"
                f"[dim]{self.invocation.display()}[/dim]\n"
                f"[dim]cwd: {self.invocation.cwd}[/dim]",
                id="run-heading",
            )
            yield RichLog(id="run-log", highlight=True, markup=False, wrap=False)
            yield Button("Close", id="close-btn")
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self._run_subprocess(), exclusive=True, name="ansible")

    def action_back(self) -> None:
        if self._proc and self._proc.returncode is None:
            self.notify("Process still running — terminating", severity="warning")
            try:
                self._proc.terminate()
            except ProcessLookupError:
                pass
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.action_back()

    async def _run_subprocess(self) -> None:
        log: RichLog = self.query_one("#run-log", RichLog)
        log.write(f"$ {self.invocation.display()}")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.invocation.argv,
                cwd=str(self.invocation.cwd),
                env=self.invocation.env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except FileNotFoundError as exc:
            log.write(f"\n[error] could not start ansible-playbook: {exc}")
            return

        assert self._proc.stdout is not None
        async for line in self._proc.stdout:
            log.write(line.decode("utf-8", errors="replace").rstrip("\n"))
        rc = await self._proc.wait()
        log.write(f"\n[exit {rc}]")
