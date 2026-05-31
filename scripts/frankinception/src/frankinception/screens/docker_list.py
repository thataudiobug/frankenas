"""Top-level docker catalog screen: list, add, edit, delete, import.

This mirrors :class:`HostListScreen` but for ``docker_containers_catalog``.
It's the entry point for everything to do with the container side of the
project. The ``H`` shortcut returns to the host-management screen.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from ..state import AppState


class DockerListScreen(Screen):
    """Container list with add / edit / delete / import / groups actions."""

    BINDINGS = [
        Binding("a", "add_container", "Add"),
        Binding("g", "manage_groups", "Groups"),
        Binding("i", "import_compose", "Import compose"),
        Binding("d", "delete_container", "Delete"),
        Binding("h", "back_to_hosts", "Hosts"),
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
                with Vertical(id="containers-pane", classes="pane"):
                    yield DataTable(
                        id="containers", cursor_type="row", zebra_stripes=True
                    )
                with Vertical(id="actions-pane", classes="pane"):
                    yield Static(self._actions_text(), id="actions")
                    yield Button("Add container (a)", id="add-btn", variant="primary")
                    yield Button("Manage groups (g)", id="groups-btn")
                    yield Button("Import compose (i)", id="import-btn")
                    yield Button("Delete (d)", id="delete-btn")
                    yield Button("Back to hosts (h)", id="hosts-btn")
        yield Footer()

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#containers", DataTable)
        table.add_columns("Container", "Image", "Groups")
        self._refill(table)
        table.focus()

    # ---- actions -----------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is None:
            return
        self._open_editor(str(event.row_key.value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "add-btn": self.action_add_container,
            "groups-btn": self.action_manage_groups,
            "import-btn": self.action_import_compose,
            "delete-btn": self.action_delete_container,
            "hosts-btn": self.action_back_to_hosts,
        }
        handler = mapping.get(event.button.id or "")
        if handler:
            handler()

    def action_add_container(self) -> None:
        self._open_editor(None)

    def action_import_compose(self) -> None:
        from .compose_import import ComposeImportScreen

        self.app.push_screen(ComposeImportScreen(self.state), self._after_subscreen)

    def action_manage_groups(self) -> None:
        from .docker_groups import DockerGroupsScreen

        self.app.push_screen(DockerGroupsScreen(self.state), self._after_subscreen)

    def action_delete_container(self) -> None:
        name = self._cursor_key()
        if name is None:
            self.notify("Pick a row first", severity="warning")
            return

        # Re-use the simple yes/no pattern from the secrets workflow.
        from .secrets import _DeleteConfirm

        def _on_confirm(yes: bool | None) -> None:
            if not yes:
                return
            self.state.delete_container(name)
            self.state.save_docker_catalog()
            self._refill(self.query_one("#containers", DataTable))
            self.notify(f"Deleted {name}", timeout=2)

        self.app.push_screen(_DeleteConfirm(name), _on_confirm)

    def action_back_to_hosts(self) -> None:
        self.app.pop_screen()

    def action_reload(self) -> None:
        # Re-read the catalog from disk in case the user edited it
        # externally between sessions of the screen.
        self.state._load_docker_catalog()  # noqa: SLF001
        self._refill(self.query_one("#containers", DataTable))
        self.notify("Reloaded docker catalog", timeout=2)

    def action_request_quit(self) -> None:
        self.app.exit()

    # ---- helpers -----------------------------------------------------

    def _open_editor(self, name: str | None) -> None:
        from .container_editor import ContainerEditorScreen

        self.app.push_screen(
            ContainerEditorScreen(self.state, name), self._after_subscreen
        )

    def _after_subscreen(self, _result: object) -> None:
        # Subscreens save the catalog directly, so we just need to refresh.
        self._refill(self.query_one("#containers", DataTable))

    def _refill(self, table: DataTable) -> None:
        table.clear()
        containers = self.state.docker_containers()
        for name in sorted(containers.keys()):
            entry = containers[name] if isinstance(containers[name], dict) else {}
            image = str(entry.get("image", "—")) if isinstance(entry, dict) else "—"
            groups = self.state.container_groups_for(name)
            table.add_row(
                name,
                image,
                ", ".join(groups) if groups else "—",
                key=name,
            )

    def _cursor_key(self) -> str | None:
        table: DataTable = self.query_one("#containers", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.ordered_rows[table.cursor_row].key
        except (AttributeError, IndexError):
            return None
        return str(row_key.value) if row_key.value is not None else None

    def _banner(self) -> str:
        path = self.state.docker_catalog_path or "(unsaved)"
        return (
            "[b]Docker catalog[/b]   "
            f"file: [cyan]{path}[/cyan]\n"
            "Enter on a row to edit. Ctrl+S after edits in any sub-screen "
            "saves to disk."
        )

    def _actions_text(self) -> str:
        return (
            "[b]Actions[/b]\n\n"
            "Enter — edit container\n"
            "a — add container\n"
            "g — manage groups\n"
            "i — import compose / docker run\n"
            "d — delete container\n"
            "h — back to host management\n"
            "r — reload from disk\n"
            "q — quit"
        )
