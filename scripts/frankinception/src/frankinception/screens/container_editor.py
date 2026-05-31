"""Edit a single container catalog entry plus its docker-group memberships.

The catalog has a small number of well-known fields (image, ports,
volumes, env, networks, devices, restart_policy, command, labels). Each
gets a dedicated editor subscreen so the user isn't faced with a huge
form. This screen acts as the dashboard for one container: it shows the
current values and routes to per-field editors.

Saves are explicit — Ctrl+S writes the in-memory dict back through
:meth:`AppState.save_docker_catalog`. Esc with unsaved edits warns first.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TextArea,
)

from .. import yaml_io
from ..state import AppState
from .catalog_picker import (
    MultiPickerScreen,
    SinglePickerScreen,
    _DiscardConfirm,
)


RESTART_POLICIES = ["no", "on-failure", "always", "unless-stopped"]


class ContainerEditorScreen(Screen):
    """Edit a single container.

    ``initial_name`` is None to create a new container.
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("g", "edit_groups", "Edit groups"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, state: AppState, initial_name: str | None) -> None:
        super().__init__()
        self.state = state
        self._is_new = initial_name is None
        self._original_name = initial_name
        # Working copy. We don't mutate the catalog dict directly until
        # save so the user can cancel cleanly.
        if initial_name and initial_name in state.docker_containers():
            existing = state.docker_containers()[initial_name]
            self._working: dict[str, Any] = (
                dict(existing) if isinstance(existing, dict) else {}
            )
            self._name = initial_name
        else:
            self._working = {}
            self._name = ""
        self._dirty = False
        # Snapshot of group memberships at edit start so we can detect
        # changes vs the live catalog.
        self._original_groups: list[str] = (
            state.container_groups_for(initial_name) if initial_name else []
        )
        self._groups: list[str] = list(self._original_groups)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(self._heading(), id="host-heading")
            with Horizontal(id="host-body"):
                with Vertical(id="meta-pane", classes="pane"):
                    yield Static("[b]Metadata[/b]")
                    yield Label("Name (catalog key):")
                    yield Input(value=self._name, id="name-input")
                    yield Label("Image:")
                    yield Input(value=str(self._working.get("image", "")), id="image-input")
                    yield Label("Restart policy:")
                    yield Static(
                        str(self._working.get("restart_policy", "(unset)")),
                        id="restart-display",
                    )
                    yield Button("Pick restart policy…", id="restart-btn")
                    yield Label("Command (one shell line, blank for none):")
                    yield Input(
                        value=self._render_command(),
                        id="command-input",
                    )
                with Vertical(id="fields-pane", classes="pane"):
                    yield Static("[b]Fields[/b]")
                    yield DataTable(
                        id="fields-table",
                        cursor_type="row",
                        zebra_stripes=True,
                    )
                with Vertical(id="groups-pane", classes="pane"):
                    yield Static("[b]Groups[/b]")
                    yield Static(self._groups_text(), id="groups-text")
                    yield Button("Edit groups (g)", id="edit-groups-btn", variant="primary")
                    yield Button("Save (Ctrl+S)", id="save-btn", variant="success")
                    yield Static(self._hint(), id="hint")
        yield Footer()

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#fields-table", DataTable)
        table.add_columns("Field", "Summary")
        self._refill_fields(table)

    # ---- input change tracking --------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        self._dirty = True

    # ---- actions -----------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "edit-groups-btn": self.action_edit_groups,
            "save-btn": self.action_save,
            "restart-btn": self._pick_restart_policy,
        }
        handler = mapping.get(event.button.id or "")
        if handler:
            handler()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is None:
            return
        self._edit_field(str(event.row_key.value))

    def action_back(self) -> None:
        if not self._dirty and self._groups == self._original_groups:
            self.app.pop_screen()
            return

        def _on_confirm(discard: bool | None) -> None:
            if discard:
                self.app.pop_screen()

        self.app.push_screen(_DiscardConfirm(), _on_confirm)

    def action_edit_groups(self) -> None:
        all_groups = self.state.docker_groups()
        entries = {g: None for g in sorted(all_groups.keys())}

        def _on_done(result: list[str] | None) -> None:
            if result is None:
                return
            self._groups = sorted(result)
            self.query_one("#groups-text", Static).update(self._groups_text())

        self.app.push_screen(
            MultiPickerScreen(
                "Docker groups for this container",
                entries,
                self._groups,
                describe=lambda _k, _v: "",
            ),
            _on_done,
        )

    def action_save(self) -> None:
        new_name = self.query_one("#name-input", Input).value.strip()
        if not new_name:
            self.notify("Container name can't be empty", severity="error")
            return

        # Pull metadata back into the working dict.
        image = self.query_one("#image-input", Input).value.strip()
        if image:
            self._working["image"] = image
        else:
            self.notify("Image is required", severity="error")
            return

        cmd_raw = self.query_one("#command-input", Input).value.strip()
        if cmd_raw:
            self._working["command"] = cmd_raw
        else:
            self._working.pop("command", None)

        # Persist to the catalog. Renames update group memberships too.
        containers = self.state.docker_containers()
        if self._is_new:
            if new_name in containers:
                self.notify(f"'{new_name}' already exists", severity="error")
                return
            containers[new_name] = self._working_for_save()
        else:
            if new_name != self._original_name:
                if new_name in containers:
                    self.notify(f"'{new_name}' already exists", severity="error")
                    return
                self.state.rename_container(self._original_name or "", new_name)
            # Replace the body so deletions of fields are honoured.
            containers[new_name] = self._working_for_save()
        self._original_name = new_name
        self._is_new = False

        # Apply group changes after the rename so memberships line up.
        self.state.set_container_groups(new_name, self._groups)
        self._original_groups = list(self._groups)

        self.state.save_docker_catalog()
        self._dirty = False
        self.notify(f"Saved {new_name}", timeout=2)

    # ---- helpers -----------------------------------------------------

    def _working_for_save(self) -> dict[str, Any]:
        """Return a YAML-safe copy of the working dict.

        We use ruamel's CommentedMap so saved files keep round-trippable
        formatting on subsequent edits.
        """
        out = yaml_io.empty_map()
        # Ordering matches the existing catalog convention:
        # image first, then ports/volumes/env/networks/devices, then
        # restart_policy/command/labels.
        for key in (
            "image",
            "ports",
            "volumes",
            "env",
            "networks",
            "devices",
            "restart_policy",
            "command",
            "labels",
        ):
            if key in self._working:
                out[key] = self._working[key]
        # Preserve any other fields we don't model explicitly.
        for k, v in self._working.items():
            if k not in out:
                out[k] = v
        return out

    def _render_command(self) -> str:
        cmd = self._working.get("command")
        if cmd is None:
            return ""
        if isinstance(cmd, list):
            import shlex

            return " ".join(shlex.quote(str(c)) for c in cmd)
        return str(cmd)

    def _heading(self) -> str:
        title = "[b]New container[/b]" if self._is_new else f"[b]{self._original_name}[/b]"
        return f"{title}   ({self.state.docker_catalog_path})"

    def _hint(self) -> str:
        return (
            "Enter on a field row to edit it.\n"
            "g — edit groups   Ctrl+S — save   Esc — back"
        )

    def _groups_text(self) -> str:
        if not self._groups:
            return "(none)"
        return "\n".join(f"• {g}" for g in self._groups)

    # ---- fields table ------------------------------------------------

    _FIELDS = ["ports", "volumes", "env", "networks", "devices", "labels"]

    def _refill_fields(self, table: DataTable) -> None:
        table.clear()
        for field in self._FIELDS:
            value = self._working.get(field)
            table.add_row(field, _summarise_field(value), key=field)

    def _edit_field(self, field: str) -> None:
        # env and labels are dict-of-strings; others are list-of-strings.
        if field in ("env", "labels"):
            current = self._working.get(field) or {}
            current = (
                {str(k): str(v) for k, v in current.items()}
                if isinstance(current, dict)
                else {}
            )

            def _on_done(new_value: dict[str, str] | None) -> None:
                if new_value is None:
                    return
                if new_value:
                    self._working[field] = new_value
                else:
                    self._working.pop(field, None)
                self._dirty = True
                self._refill_fields(self.query_one("#fields-table", DataTable))

            self.app.push_screen(
                _DictFieldEditor(self.state, field, current), _on_done
            )
        else:
            current = self._working.get(field) or []
            current = [str(v) for v in current] if isinstance(current, list) else []

            def _on_done(new_value: list[str] | None) -> None:
                if new_value is None:
                    return
                if new_value:
                    self._working[field] = new_value
                else:
                    self._working.pop(field, None)
                self._dirty = True
                self._refill_fields(self.query_one("#fields-table", DataTable))

            self.app.push_screen(
                _ListFieldEditor(self.state, field, current), _on_done
            )

    def _pick_restart_policy(self) -> None:
        entries = {p: None for p in RESTART_POLICIES}
        current = self._working.get("restart_policy")

        def _on_pick(value: str | None) -> None:
            if value is None and current is not None:
                # User cleared it.
                self._working.pop("restart_policy", None)
            elif value is not None:
                self._working["restart_policy"] = value
            self._dirty = True
            self.query_one("#restart-display", Static).update(
                str(self._working.get("restart_policy", "(unset)"))
            )

        self.app.push_screen(
            SinglePickerScreen(
                "Restart policy",
                entries,
                current if isinstance(current, str) else None,
            ),
            _on_pick,
        )


def _summarise_field(value: Any) -> str:
    if value is None or value == [] or value == {}:
        return "[dim]—[/dim]"
    if isinstance(value, list):
        return f"{len(value)} entr{'y' if len(value) == 1 else 'ies'}: " + ", ".join(
            _truncate(str(v)) for v in value[:3]
        ) + ("…" if len(value) > 3 else "")
    if isinstance(value, dict):
        keys = list(value.keys())
        return f"{len(keys)} entr{'y' if len(keys) == 1 else 'ies'}: " + ", ".join(
            keys[:3]
        ) + ("…" if len(keys) > 3 else "")
    return _truncate(str(value))


def _truncate(value: str, limit: int = 40) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"


# ---- list-field editor ------------------------------------------------------


class _ListFieldEditor(ModalScreen[list[str] | None]):
    """Edit a list of strings (volumes, ports, networks, devices).

    Volumes get the manual-volume editor for each row (so you keep the
    bind insertion behaviour); other lists use a plain string editor.
    """

    BINDINGS = [
        Binding("a", "add", "Add"),
        Binding("d", "delete", "Delete"),
        Binding("ctrl+s", "save", "Save"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, state: AppState, field: str, values: list[str]) -> None:
        super().__init__()
        self.state = state
        self.field = field
        self.values = list(values)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="vol-edit"):
            yield Static(f"[b]{self.field}[/b]   Enter to edit a row, a to add, d to delete")
            with Vertical(classes="pane"):
                yield DataTable(id="rows", cursor_type="row", zebra_stripes=True)
            with Horizontal(classes="vol-row"):
                yield Button("Add (a)", id="add-btn")
                yield Button("Delete (d)", id="delete-btn")
                yield Button("Save (Ctrl+S)", id="save-btn", variant="primary")
                yield Button("Cancel (Esc)", id="cancel-btn")
        yield Footer()

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#rows", DataTable)
        table.add_columns("#", "Value")
        self._refill()
        table.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "add-btn": self.action_add,
            "delete-btn": self.action_delete,
            "save-btn": self.action_save,
            "cancel-btn": self.action_cancel,
        }
        handler = mapping.get(event.button.id or "")
        if handler:
            handler()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is None:
            return
        idx = int(str(event.row_key.value))
        self._edit(idx)

    def action_add(self) -> None:
        self._edit(None)

    def action_delete(self) -> None:
        idx = self._cursor_idx()
        if idx is None:
            return
        del self.values[idx]
        self._refill()

    def action_save(self) -> None:
        self.dismiss(self.values)

    def action_cancel(self) -> None:
        self.dismiss(None)

    # ---- helpers -----------------------------------------------------

    def _edit(self, idx: int | None) -> None:
        if self.field == "volumes":
            self._edit_volume(idx)
        else:
            self._edit_simple(idx)

    def _edit_volume(self, idx: int | None) -> None:
        from ..bind_mapper import VolumeMatch
        from .compose_import import _ManualVolumeScreen

        current = self.values[idx] if idx is not None else ""
        seed = current
        match = VolumeMatch(
            rendered=seed,
            bind_key=None,
            raw_host_path="",
            raw_container_path="",
            mode=None,
            needs_user_choice=False,
        )

        def _on_done(value: str | None) -> None:
            if value is None:
                return
            if idx is None:
                self.values.append(value)
            else:
                self.values[idx] = value
            self._refill()

        self.app.push_screen(_ManualVolumeScreen(self.state, match, seed), _on_done)

    def _edit_simple(self, idx: int | None) -> None:
        current = self.values[idx] if idx is not None else ""

        def _on_done(value: str | None) -> None:
            if value is None:
                return
            if idx is None:
                self.values.append(value)
            else:
                self.values[idx] = value
            self._refill()

        self.app.push_screen(
            _StringEditor(f"{self.field} entry", current), _on_done
        )

    def _refill(self) -> None:
        table: DataTable = self.query_one("#rows", DataTable)
        table.clear()
        for i, value in enumerate(self.values):
            table.add_row(str(i + 1), _truncate(value, 80), key=str(i))

    def _cursor_idx(self) -> int | None:
        table: DataTable = self.query_one("#rows", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.ordered_rows[table.cursor_row].key
        except (AttributeError, IndexError):
            return None
        if row_key.value is None:
            return None
        return int(str(row_key.value))


# ---- dict-field editor (env, labels) ----------------------------------------


class _DictFieldEditor(ModalScreen[dict[str, str] | None]):
    """Edit a string-to-string mapping. Used for ``env`` and ``labels``.

    The value editor for ``env`` rows opens with the same insertion
    helpers (Ctrl+B / Ctrl+V / Ctrl+N) as the variable resolver — so
    secrets can be referenced without bouncing back to the secrets
    workflow.
    """

    BINDINGS = [
        Binding("a", "add", "Add"),
        Binding("d", "delete", "Delete"),
        Binding("ctrl+s", "save", "Save"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, state: AppState, field: str, values: dict[str, str]) -> None:
        super().__init__()
        self.state = state
        self.field = field
        self.values = dict(values)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="vol-edit"):
            yield Static(f"[b]{self.field}[/b]   Enter to edit a row, a to add, d to delete")
            with Vertical(classes="pane"):
                yield DataTable(id="rows", cursor_type="row", zebra_stripes=True)
            with Horizontal(classes="vol-row"):
                yield Button("Add (a)", id="add-btn")
                yield Button("Delete (d)", id="delete-btn")
                yield Button("Save (Ctrl+S)", id="save-btn", variant="primary")
                yield Button("Cancel (Esc)", id="cancel-btn")
        yield Footer()

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#rows", DataTable)
        table.add_columns("Key", "Value")
        self._refill()
        table.focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "add-btn": self.action_add,
            "delete-btn": self.action_delete,
            "save-btn": self.action_save,
            "cancel-btn": self.action_cancel,
        }
        handler = mapping.get(event.button.id or "")
        if handler:
            handler()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is None:
            return
        self._edit(str(event.row_key.value))

    def action_add(self) -> None:
        self._edit(None)

    def action_delete(self) -> None:
        key = self._cursor_key()
        if key is None:
            return
        self.values.pop(key, None)
        self._refill()

    def action_save(self) -> None:
        self.dismiss(self.values)

    def action_cancel(self) -> None:
        self.dismiss(None)

    # ---- helpers -----------------------------------------------------

    def _edit(self, key: str | None) -> None:
        current_value = self.values.get(key, "") if key else ""

        def _on_done(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            new_key, new_value = result
            # Renames remove the old entry — but only if the old key
            # still exists; if it was deleted concurrently, just write
            # the new one.
            if key is not None and key != new_key:
                self.values.pop(key, None)
            self.values[new_key] = new_value
            self._refill()

        offer_secret_helpers = self.field == "env"
        self.app.push_screen(
            _KeyValueEditor(
                self.state,
                self.field,
                initial_key=key or "",
                initial_value=current_value,
                offer_secret_helpers=offer_secret_helpers,
            ),
            _on_done,
        )

    def _refill(self) -> None:
        table: DataTable = self.query_one("#rows", DataTable)
        table.clear()
        for k in sorted(self.values.keys()):
            table.add_row(k, _truncate(self.values[k], 60), key=k)

    def _cursor_key(self) -> str | None:
        table: DataTable = self.query_one("#rows", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.ordered_rows[table.cursor_row].key
        except (AttributeError, IndexError):
            return None
        return str(row_key.value) if row_key.value is not None else None


# ---- one-string editor ------------------------------------------------------


class _StringEditor(ModalScreen[str | None]):
    """Plain string editor. Returns the new value or None on cancel."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, label: str, initial: str) -> None:
        super().__init__()
        self._label = label
        self._initial = initial

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="vol-edit"):
            yield Static(f"[b]{self._label}[/b]")
            yield Input(value=self._initial, id="value-input")
            with Horizontal(classes="vol-row"):
                yield Button("Save (Ctrl+S)", id="save-btn", variant="primary")
                yield Button("Cancel (Esc)", id="cancel-btn")
        yield Footer()

    def on_mount(self) -> None:
        inp: Input = self.query_one("#value-input", Input)
        inp.focus()
        inp.cursor_position = len(self._initial)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self.action_save()
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def action_save(self) -> None:
        value = self.query_one("#value-input", Input).value
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class _KeyValueEditor(ModalScreen[tuple[str, str] | None]):
    """Edit a single key/value pair.

    When ``offer_secret_helpers`` is true, the value field gets the same
    Ctrl+B / Ctrl+V / Ctrl+N insertion shortcuts as the variable resolver
    so env var values can reference binds and secrets without leaving
    the workflow.
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+b", "insert_bind", "Insert bind"),
        Binding("ctrl+v", "insert_vault", "Insert vault secret"),
        Binding("ctrl+n", "create_vault", "New vault secret"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        state: AppState,
        field: str,
        initial_key: str,
        initial_value: str,
        offer_secret_helpers: bool,
    ) -> None:
        super().__init__()
        self.state = state
        self.field = field
        self._initial_key = initial_key
        self._initial_value = initial_value
        self._offer_secret_helpers = offer_secret_helpers

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="vol-edit"):
            yield Static(f"[b]{self.field} entry[/b]")
            yield Label("Key:")
            yield Input(value=self._initial_key, id="key-input")
            yield Label("Value:")
            yield Input(value=self._initial_value, id="value-input")
            with Horizontal(classes="vol-row"):
                if self._offer_secret_helpers:
                    yield Button("Insert bind (Ctrl+B)", id="insert-bind-btn")
                    yield Button("Insert vault (Ctrl+V)", id="insert-vault-btn")
                    yield Button("New vault key (Ctrl+N)", id="new-vault-btn")
                yield Button("Save (Ctrl+S)", id="save-btn", variant="primary")
                yield Button("Cancel (Esc)", id="cancel-btn")
        yield Footer()

    def on_mount(self) -> None:
        # Land on whichever field is empty — usually that's value when
        # editing existing entries, key for new ones.
        target_id = "key-input" if not self._initial_key else "value-input"
        inp: Input = self.query_one(f"#{target_id}", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "save-btn": self.action_save,
            "cancel-btn": self.action_cancel,
            "insert-bind-btn": self.action_insert_bind,
            "insert-vault-btn": self.action_insert_vault,
            "new-vault-btn": self.action_create_vault,
        }
        handler = mapping.get(event.button.id or "")
        if handler:
            handler()

    def action_save(self) -> None:
        key = self.query_one("#key-input", Input).value.strip()
        value = self.query_one("#value-input", Input).value
        if not key:
            self.notify("Key cannot be empty", severity="error")
            return
        self.dismiss((key, value))

    def action_cancel(self) -> None:
        self.dismiss(None)

    # ---- inserters route through the shared helpers ------------------

    def action_insert_bind(self) -> None:
        if not self._offer_secret_helpers:
            return
        from . import _inserters

        _inserters.insert_bind_var(self, self.state, "value-input")

    def action_insert_vault(self) -> None:
        if not self._offer_secret_helpers:
            return
        from . import _inserters

        _inserters.insert_vault_ref(self, self.state, "value-input")

    def action_create_vault(self) -> None:
        if not self._offer_secret_helpers:
            return
        from . import _inserters

        key = self.query_one("#key-input", Input).value.strip()
        suggested = _inserters.suggest_vault_key(key or "secret")
        _inserters.create_vault_key(self, self.state, "value-input", suggested)
