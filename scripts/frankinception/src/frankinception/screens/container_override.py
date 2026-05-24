"""Per-host container override editor.

Lists every container the host would deploy (from group memberships +
direct enables) and lets the user edit ``docker_containers_overrides``
fields for one container at a time. Common knobs (state, restart policy,
image tag pin) get dedicated forms; arbitrary keys can also be edited
through a free-form input.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from ..state import AppState


class ContainerOverrideScreen(Screen):
    BINDINGS = [
        Binding("s", "save", "Save"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, state: AppState, host: str) -> None:
        super().__init__()
        self.state = state
        self.host = host

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(
                f"[b]Container overrides for {self.host}[/b]\n"
                "Enter on a container to edit; s to save, Esc to back",
                id="overrides-heading",
            )
            yield DataTable(
                id="containers", cursor_type="row", zebra_stripes=True
            )
        yield Footer()

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#containers", DataTable)
        table.add_columns("Container", "Image (catalog)", "Override summary")
        self._refill(table)
        table.focus()

    def action_save(self) -> None:
        self.state.host_vars(self.host).save()
        self.notify(f"Saved overrides for {self.host}", timeout=2)

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is None:
            return
        container = str(event.row_key.value)

        def _on_done(_: object) -> None:
            self._refill(self.query_one("#containers", DataTable))

        self.app.push_screen(
            _ContainerFieldEditor(self.state, self.host, container),
            _on_done,
        )

    # ---- helpers -----------------------------------------------------

    def _refill(self, table: DataTable) -> None:
        table.clear()
        catalog = (self.state.docker_catalog or {}).get("docker_containers_catalog") or {}
        groups_catalog = (self.state.docker_catalog or {}).get("docker_groups_catalog") or {}
        host_vars = self.state.host_vars(self.host)

        # Build the list of containers this host would deploy: union of
        # docker_groups_enabled (expanded via docker_groups_catalog) and
        # docker_containers_enabled.
        containers: set[str] = set()
        groups_enabled = host_vars.get("docker_groups_enabled") or {}
        if isinstance(groups_enabled, dict):
            for grp in groups_enabled.keys():
                grp_body = groups_catalog.get(grp) or {}
                if isinstance(grp_body, dict):
                    containers.update(grp_body.keys())
        direct = host_vars.get("docker_containers_enabled") or {}
        if isinstance(direct, dict):
            containers.update(direct.keys())

        overrides = host_vars.get("docker_containers_overrides") or {}
        for name in sorted(containers):
            cat_entry = catalog.get(name) or {}
            image = cat_entry.get("image", "—") if isinstance(cat_entry, dict) else "—"
            ovr = overrides.get(name) if isinstance(overrides, dict) else None
            ovr_summary = (
                ", ".join(f"{k}={_truncate(v)}" for k, v in ovr.items())
                if isinstance(ovr, dict) and ovr
                else "—"
            )
            table.add_row(name, image, ovr_summary, key=name)


def _truncate(value: object, limit: int = 30) -> str:
    s = str(value)
    return s if len(s) <= limit else s[: limit - 1] + "…"


class _ContainerFieldEditor(ModalScreen[None]):
    """Edit a few common override fields for one container."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, state: AppState, host: str, container: str) -> None:
        super().__init__()
        self.state = state
        self.host = host
        self.container = container

    def compose(self) -> ComposeResult:
        host_vars = self.state.host_vars(self.host)
        overrides = host_vars.container_overrides().get(self.container) or {}
        current_state = overrides.get("state", "")
        current_restart = overrides.get("restart_policy", "")
        current_image = overrides.get("image", "")

        yield Header(show_clock=False)
        with Vertical(id="ctnr-edit"):
            yield Static(f"[b]{self.container}[/b] override on {self.host}")
            yield Static(
                "Empty value clears the override and falls back to the catalog.",
                classes="dim",
            )
            yield Label("state (started/stopped/absent):")
            yield Input(value=str(current_state), id="state-input")
            yield Label("restart_policy (always/unless-stopped/no/on-failure):")
            yield Input(value=str(current_restart), id="restart-input")
            yield Label("image (override the catalog image, e.g. pin a tag):")
            yield Input(value=str(current_image), id="image-input")
            yield Button("Save", id="save-btn", variant="primary")
            yield Button("Cancel", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)
            return
        if event.button.id != "save-btn":
            return
        host_vars = self.state.host_vars(self.host)
        for input_id, key in [
            ("state-input", "state"),
            ("restart-input", "restart_policy"),
            ("image-input", "image"),
        ]:
            value = self.query_one(f"#{input_id}", Input).value.strip() or None
            host_vars.set_container_override(self.container, key, value)
        self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
