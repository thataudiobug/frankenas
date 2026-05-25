"""Pick a playbook, choose a limit scope, and run it.

Flow:

1. ``PlayRunnerScreen`` — list of playbooks; Enter on a row chooses it.
2. ``_LimitTypeScreen`` — three options: no limit, limit by host, limit by
   group. Returned value drives step 3.
3. ``SinglePickerScreen`` (re-used) — only shown for the host/group choices,
   pre-populated from the inventory.
4. ``_RunOutputScreen`` — streams ansible-playbook output live.

Check mode is a top-level toggle that applies to whatever runs.
"""

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
    Label,
    RichLog,
    Static,
)

from .. import runner
from ..plays import Playbook, list_playbooks
from ..state import AppState
from .catalog_picker import SinglePickerScreen


class PlayRunnerScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, state: AppState, default_limit: str | None = None) -> None:
        super().__init__()
        self.state = state
        # ``default_limit`` is accepted for backwards-compat with callers but
        # ignored — limit selection is now an explicit step after picking a
        # playbook so the user isn't surprised by which host they're targeting.
        del default_limit

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(
                f"[b]Run playbook[/b]   plays dir: [cyan]{self.state.layout.plays_dir}[/cyan]\n"
                "Enter on a row to choose a limit scope, then run.",
                id="play-heading",
            )
            yield DataTable(id="plays", cursor_type="row", zebra_stripes=True)
            with Horizontal(id="play-options"):
                yield Checkbox("Check mode (--check)", id="check-cb")
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

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is not None:
            self._choose_limit_then_run(str(event.row_key.value))

    # ---- limit-scope flow -------------------------------------------

    def _choose_limit_then_run(self, name: str) -> None:
        play = next((p for p in self._plays if p.name == name), None)
        if play is None:
            return

        def _on_scope(choice: str | None) -> None:
            # choice is "none" | "host" | "group" | None (cancel)
            if choice is None:
                return
            if choice == "none":
                self._run(play, limit=None)
                return
            if choice == "host":
                self._pick_host_then_run(play)
            elif choice == "group":
                self._pick_group_then_run(play)

        self.app.push_screen(_LimitTypeScreen(play.name), _on_scope)

    def _pick_host_then_run(self, play: Playbook) -> None:
        hosts = self.state.inventory.hosts()
        if not hosts:
            self.notify("No hosts in inventory", severity="warning")
            return
        # Build a "host -> groups" describe so the user has context. The
        # picker accepts a dict, so we use None values purely for keys.
        entries = {h: None for h in hosts}
        inv = self.state.inventory

        def describe(key: str, _value: object) -> str:
            direct = inv.direct_groups_of(key)
            return ", ".join(direct) if direct else ""

        def _on_pick(host: str | None) -> None:
            if host:
                self._run(play, limit=host)

        self.app.push_screen(
            SinglePickerScreen(
                f"Limit '{play.name}' to host", entries, None, describe=describe
            ),
            _on_pick,
        )

    def _pick_group_then_run(self, play: Playbook) -> None:
        groups = sorted(self.state.inventory.groups())
        if not groups:
            self.notify("No groups in inventory", severity="warning")
            return
        entries = {g: None for g in groups}
        inv = self.state.inventory

        def describe(key: str, _value: object) -> str:
            # Count how many hosts the group covers including children.
            count = sum(1 for h in inv.hosts() if key in inv.all_groups_of(h))
            return f"{count} host{'s' if count != 1 else ''}"

        def _on_pick(group: str | None) -> None:
            if group:
                self._run(play, limit=group)

        self.app.push_screen(
            SinglePickerScreen(
                f"Limit '{play.name}' to group", entries, None, describe=describe
            ),
            _on_pick,
        )

    def _run(self, play: Playbook, limit: str | None) -> None:
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


class _LimitTypeScreen(ModalScreen[str | None]):
    """Three-way modal: no limit, by host, or by group.

    Returns one of ``"none"`` / ``"host"`` / ``"group"`` (or None on cancel).
    """

    BINDINGS = [
        Binding("n", "no_limit", "No limit"),
        Binding("h", "by_host", "Host"),
        Binding("g", "by_group", "Group"),
        Binding("up", "focus_previous", "Prev", show=False),
        Binding("down", "focus_next", "Next", show=False),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, play_name: str) -> None:
        super().__init__()
        self._play_name = play_name

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="bind-choice"):
            yield Static(
                f"[b]Limit scope for[/b] {self._play_name}\n"
                "How should this run be scoped?",
                id="bind-info",
            )
            yield Label("Pick an option (click or press the highlighted key):")
            yield Button(
                "[u]N[/u]o limit (run on all hosts the play targets)",
                id="none-btn",
                variant="primary",
            )
            yield Button("Limit by [u]h[/u]ost…", id="host-btn")
            yield Button("Limit by [u]g[/u]roup…", id="group-btn")
            yield Button("Cancel (Esc)", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "none-btn":
            self.dismiss("none")
        elif event.button.id == "host-btn":
            self.dismiss("host")
        elif event.button.id == "group-btn":
            self.dismiss("group")
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def action_no_limit(self) -> None:
        self.dismiss("none")

    def action_by_host(self) -> None:
        self.dismiss("host")

    def action_by_group(self) -> None:
        self.dismiss("group")

    def action_cancel(self) -> None:
        self.dismiss(None)


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
