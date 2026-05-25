"""Per-host editor: groups, catalog selections, container overrides."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Footer, Header, Static

from ..catalogs import Catalog, CatalogKind
from ..hostvars import HostVars
from ..inventory import list_assignable_groups
from ..state import AppState
from .catalog_picker import MultiPickerScreen, SinglePickerScreen


class HostEditorScreen(Screen):
    """Edit one host: group membership and ``*_enabled`` selections."""

    BINDINGS = [
        Binding("g", "edit_groups", "Edit groups"),
        Binding("s", "save", "Save"),
        Binding("o", "edit_overrides", "Container overrides"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, state: AppState, host: str) -> None:
        super().__init__()
        self.state = state
        self.host = host
        self._dirty = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(self._heading(), id="host-heading")
            with Horizontal(id="host-body"):
                with Vertical(id="catalog-pane", classes="pane"):
                    yield Static("[b]Catalog selections[/b]", id="catalog-pane-title")
                    yield DataTable(
                        id="catalog-table",
                        cursor_type="row",
                        zebra_stripes=True,
                    )
                with Vertical(id="groups-pane", classes="pane"):
                    yield Static("[b]Groups[/b]", id="groups-title")
                    yield Static(self._groups_text(), id="groups-text")
                    yield Button("Edit groups (g)", id="edit-groups-btn", variant="primary")
                    yield Button("Container overrides (o)", id="overrides-btn")
                    yield Static(self._hint_text(), id="hint")
        yield Footer()

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#catalog-table", DataTable)
        table.add_columns("Catalog", "Kind", "From group", "Selection")
        self._refill_catalog_table(table)
        table.focus()

    # ---- actions -----------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        # Buttons need explicit dispatch — the screen-level keybinds and the
        # buttons share the same actions, but Textual won't auto-link them.
        if event.button.id == "edit-groups-btn":
            self.action_edit_groups()
        elif event.button.id == "overrides-btn":
            self.action_edit_overrides()

    def action_back(self) -> None:
        if self._dirty:
            self.notify("Unsaved changes — press s to save or Esc again", severity="warning")
            self._dirty = False  # second Esc actually leaves
            return
        self.app.pop_screen()

    def action_save(self) -> None:
        host_vars = self.state.host_vars(self.host)
        host_vars.save()
        self.state.inventory.save()
        self._dirty = False
        self.notify(f"Saved {self.host}", timeout=2)

    def action_edit_groups(self) -> None:
        inv = self.state.inventory
        all_groups = list_assignable_groups(inv)
        current = inv.direct_groups_of(self.host)
        entries = {g: None for g in all_groups}

        def _on_done(result: list[str] | None) -> None:
            if result is None:
                return
            target = set(result)
            current_set = set(current)
            for g in current_set - target:
                inv.remove_host_from_group(self.host, g)
            for g in target - current_set:
                inv.add_host_to_group(self.host, g)
            self._dirty = True
            self.query_one("#groups-text", Static).update(self._groups_text())
            self._refill_catalog_table(self.query_one("#catalog-table", DataTable))

        self.app.push_screen(
            MultiPickerScreen(
                f"Groups for {self.host}",
                entries,
                current,
                describe=lambda _k, _v: "",
            ),
            _on_done,
        )

    def action_edit_overrides(self) -> None:
        from .container_override import ContainerOverrideScreen

        if "docker" not in self.state.inventory.all_groups_of(self.host):
            self.notify("Host is not in the docker group", severity="warning")
            return
        self.app.push_screen(ContainerOverrideScreen(self.state, self.host))

    # ---- catalog row interaction ------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is None:
            return
        catalog = self._find_catalog_by_name(str(event.row_key.value))
        if catalog is None:
            return
        self._edit_catalog(catalog)

    def _edit_catalog(self, catalog: Catalog) -> None:
        host_vars = self.state.host_vars(self.host)

        if catalog.kind is CatalogKind.SCALAR:
            current = host_vars.selection(catalog)
            current_str = str(current) if current is not None else None

            def _on_single(result: str | None) -> None:
                # SinglePicker uses None for both clear and cancel; treat
                # any return as the new value (we passed current into it).
                if result == current_str:
                    return
                host_vars.set_scalar(catalog, result)
                self._dirty = True
                self._refill_catalog_table(self.query_one("#catalog-table", DataTable))

            self.app.push_screen(
                SinglePickerScreen(
                    catalog.display_name, catalog.entries, current_str
                ),
                _on_single,
            )
        else:
            current = host_vars.selected_keys(catalog)

            def _on_multi(result: list[str] | None) -> None:
                if result is None:
                    return
                if catalog.kind is CatalogKind.LIST:
                    host_vars.set_list(catalog, result)
                else:
                    host_vars.set_mapping(catalog, result)
                self._dirty = True
                self._refill_catalog_table(self.query_one("#catalog-table", DataTable))

            self.app.push_screen(
                MultiPickerScreen(
                    catalog.display_name, catalog.entries, current
                ),
                _on_multi,
            )

    # ---- rendering ---------------------------------------------------

    def _heading(self) -> str:
        return f"[b]{self.host}[/b]   ({self.state.layout.host_vars_dir / (self.host + '.yml')})"

    def _hint_text(self) -> str:
        return (
            "Enter on a catalog row to edit it.\n"
            "g — edit groups   o — container overrides\n"
            "s — save   Esc — back"
        )

    def _groups_text(self) -> str:
        inv = self.state.inventory
        direct = inv.direct_groups_of(self.host)
        inherited = [g for g in inv.all_groups_of(self.host) if g not in direct]
        lines = ["[b]Direct[/b]"]
        lines.extend(f"  • {g}" for g in direct) if direct else lines.append("  (none)")
        lines.append("")
        lines.append("[b]Inherited[/b]")
        if inherited:
            lines.extend(f"  • {g}" for g in inherited)
        else:
            lines.append("  (none)")
        return "\n".join(lines)

    def _refill_catalog_table(self, table: DataTable) -> None:
        table.clear()
        host_vars: HostVars = self.state.host_vars(self.host)
        catalogs = self.state.catalogs_for(self.host)
        self._catalogs = catalogs  # keep for row lookup
        for cat in catalogs:
            selection = host_vars.selected_keys(cat)
            if cat.kind is CatalogKind.SCALAR:
                shown = selection[0] if selection else "—"
            else:
                shown = ", ".join(selection) if selection else "—"
            table.add_row(
                cat.display_name,
                cat.kind.value,
                cat.group,
                shown,
                key=cat.name,
            )

    def _find_catalog_by_name(self, name: str) -> Catalog | None:
        for c in getattr(self, "_catalogs", []):
            if c.name == name:
                return c
        return None
