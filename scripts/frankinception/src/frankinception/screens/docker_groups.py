"""Add / rename / delete entries in ``docker_groups_catalog``.

Each docker group is a named bundle of containers — host_vars use
``docker_groups_enabled: { public:, piracy: }`` to opt a host into the
bundles it wants. This screen lets the user maintain the bundles
themselves; member assignment for individual containers happens on the
container editor.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from .. import yaml_io
from ..state import AppState
from .secrets import _DeleteConfirm


class DockerGroupsScreen(Screen):
    """Top-level group management."""

    BINDINGS = [
        Binding("a", "add_group", "Add"),
        Binding("e", "edit_group", "Rename"),
        Binding("d", "delete_group", "Delete"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(
                "[b]Docker groups[/b]   "
                "Bundles of containers referenced from host_vars.\n"
                "Enter on a row to rename it. a to add, d to delete, Esc to back.",
                id="banner",
            )
            with Vertical(classes="pane"):
                yield DataTable(id="groups-table", cursor_type="row", zebra_stripes=True)
            with Horizontal(classes="vol-row"):
                yield Button("Add (a)", id="add-btn", variant="primary")
                yield Button("Rename (e)", id="edit-btn")
                yield Button("Delete (d)", id="delete-btn")
                yield Button("Back (Esc)", id="back-btn")
        yield Footer()

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#groups-table", DataTable)
        table.add_columns("Group", "Members")
        self._refill()
        table.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "add-btn": self.action_add_group,
            "edit-btn": self.action_edit_group,
            "delete-btn": self.action_delete_group,
            "back-btn": self.action_back,
        }
        handler = mapping.get(event.button.id or "")
        if handler:
            handler()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is not None:
            self._rename(str(event.row_key.value))

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_add_group(self) -> None:
        def _on_done(name: str | None) -> None:
            if not name:
                return
            groups = self.state.docker_groups()
            if name in groups:
                self.notify(f"Group '{name}' already exists", severity="error")
                return
            groups[name] = yaml_io.empty_map()
            self.state.save_docker_catalog()
            self._refill()
            self.notify(f"Created group {name}", timeout=2)

        self.app.push_screen(_GroupNameEditor("New docker group", ""), _on_done)

    def action_edit_group(self) -> None:
        name = self._cursor_key()
        if name is None:
            self.notify("Pick a row first", severity="warning")
            return
        self._rename(name)

    def action_delete_group(self) -> None:
        name = self._cursor_key()
        if name is None:
            self.notify("Pick a row first", severity="warning")
            return

        def _on_confirm(yes: bool | None) -> None:
            if not yes:
                return
            self.state.delete_docker_group(name)
            self.state.save_docker_catalog()
            self._refill()
            self.notify(f"Deleted group {name}", timeout=2)

        self.app.push_screen(_DeleteConfirm(name), _on_confirm)

    # ---- helpers -----------------------------------------------------

    def _rename(self, old: str) -> None:
        def _on_done(new: str | None) -> None:
            if new is None or new == old:
                return
            try:
                self.state.rename_docker_group(old, new)
            except KeyError as exc:
                self.notify(str(exc), severity="error")
                return
            self.state.save_docker_catalog()
            self._refill()
            self.notify(f"Renamed to {new}", timeout=2)

        self.app.push_screen(_GroupNameEditor(f"Rename {old}", old), _on_done)

    def _refill(self) -> None:
        table: DataTable = self.query_one("#groups-table", DataTable)
        table.clear()
        groups = self.state.docker_groups()
        for name in sorted(groups.keys()):
            members = groups[name]
            count = len(members) if isinstance(members, dict) else 0
            preview = (
                ", ".join(list(members.keys())[:5]) if isinstance(members, dict) else ""
            )
            if isinstance(members, dict) and len(members) > 5:
                preview += "…"
            label = f"{count} container{'s' if count != 1 else ''}"
            if preview:
                label = f"{label}  [dim]({preview})[/dim]"
            table.add_row(name, label, key=name)

    def _cursor_key(self) -> str | None:
        table: DataTable = self.query_one("#groups-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.ordered_rows[table.cursor_row].key
        except (AttributeError, IndexError):
            return None
        return str(row_key.value) if row_key.value is not None else None


class _GroupNameEditor(ModalScreen[str | None]):
    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, title: str, initial: str) -> None:
        super().__init__()
        self._title = title
        self._initial = initial

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="vol-edit"):
            yield Static(f"[b]{self._title}[/b]")
            yield Label("Group name:")
            yield Input(value=self._initial, id="name-input")
            with Horizontal(classes="vol-row"):
                yield Button("Save (Ctrl+S)", id="save-btn", variant="primary")
                yield Button("Cancel (Esc)", id="cancel-btn")
        yield Footer()

    def on_mount(self) -> None:
        inp: Input = self.query_one("#name-input", Input)
        inp.focus()
        inp.cursor_position = len(self._initial)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self.action_save()
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def action_save(self) -> None:
        name = self.query_one("#name-input", Input).value.strip()
        if not name:
            self.notify("Name can't be empty", severity="error")
            return
        if any(c.isspace() for c in name):
            self.notify("Name can't contain whitespace", severity="error")
            return
        self.dismiss(name)

    def action_cancel(self) -> None:
        self.dismiss(None)
