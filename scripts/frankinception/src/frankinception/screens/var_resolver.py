"""Variable resolution UI for the compose import flow.

After a compose file (or ``docker run`` command) is parsed, we scan every
string field for ``${VAR}`` references. This screen presents one row per
distinct variable with its current default and resolution, and lets the
user open an editor for any of them.

The editor offers four insertion sources:

* Free-form typing
* ``Ctrl+B`` — paste a ``{{ docker_bind_catalog.<key>.mnt }}`` snippet
* ``Ctrl+V`` — paste a ``{{ vault_<name> }}`` reference (no plaintext leaves
  the vault)
* ``Ctrl+N`` — create a new vault key inline and reference it
* ``Ctrl+E`` — pick a value out of any ``.env`` file we discovered next
  to the compose source

The resolution is stored on the :class:`VarToResolve` and applied to
every container in :func:`apply_resolutions` once the user is done.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from ..compose_vars import VarToResolve
from ..state import AppState


class VariableResolverScreen(ModalScreen[list[VarToResolve] | None]):
    """List every detected ``${VAR}`` and let the user resolve each one.

    Dismisses with the (mutated) list of :class:`VarToResolve` objects on
    Continue, or ``None`` on cancel. Caller passes the screen to
    ``push_screen`` with a callback in the standard Textual pattern.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        state: AppState,
        variables: list[VarToResolve],
        env_vars: dict[str, str],
    ) -> None:
        super().__init__()
        self.state = state
        self.variables = variables
        self.env_vars = env_vars

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(
                "[b]Resolve compose variables[/b]\n"
                "Each ``${VAR}`` reference is shown below. "
                "Enter on a row to edit it. "
                "Variables with a default can be left untouched — the default will be used.",
                id="map-help",
            )
            with Vertical(classes="pane"):
                yield DataTable(
                    id="vars-table", cursor_type="row", zebra_stripes=True
                )
            with Horizontal(classes="vol-row"):
                yield Button(
                    "Continue", id="continue-btn", variant="primary"
                )
                yield Button("Cancel", id="cancel-btn")
        yield Footer()

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#vars-table", DataTable)
        table.add_columns("Variable", "Default", "Resolution", "Used in")
        self._refill()
        table.focus()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "continue-btn":
            self._continue()
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is None:
            return
        idx = int(str(event.row_key.value))
        if idx >= len(self.variables):
            return
        var = self.variables[idx]

        def _on_edit(new_value: str | None) -> None:
            if new_value is not None:
                var.resolved = new_value
                self._refill()

        self.app.push_screen(
            _VariableEditorScreen(self.state, var, self.env_vars), _on_edit
        )

    # ---- helpers -----------------------------------------------------

    def _continue(self) -> None:
        unresolved = [v for v in self.variables if not v.can_proceed]
        if unresolved:
            names = ", ".join(v.name for v in unresolved)
            self.notify(
                f"{len(unresolved)} variable(s) still need a value: {names}",
                severity="warning",
                timeout=5,
            )
            return
        # Hand the (mutated) list back to the caller via dismiss. The
        # caller's push_screen callback is responsible for whatever screen
        # comes next — pushing one ourselves here while we're still on
        # the stack would race the pop and confuse Textual's mounting
        # (see commit history for the HeaderTitle NoMatches crash that
        # came from doing it the other way around).
        self.dismiss(self.variables)

    def _refill(self) -> None:
        table: DataTable = self.query_one("#vars-table", DataTable)
        table.clear()
        for idx, var in enumerate(self.variables):
            default = var.default if var.default is not None else "[dim]—[/dim]"
            if var.has_resolution:
                resolution = _truncate(var.resolved or "")
            elif var.has_default:
                resolution = f"[dim](default: {_truncate(var.default or '')})[/dim]"
            else:
                resolution = "[red]unresolved[/red]"
            table.add_row(
                var.name,
                _truncate(default),
                resolution,
                _truncate(var.usage_summary),
                key=str(idx),
            )


def _truncate(value: str, limit: int = 60) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


class _VariableEditorScreen(ModalScreen[str | None]):
    """Edit a single variable. Returns the new value or None on cancel."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+b", "insert_bind", "Insert bind"),
        Binding("ctrl+v", "insert_vault", "Insert vault secret"),
        Binding("ctrl+n", "create_vault", "New vault secret"),
        Binding("ctrl+e", "insert_env", "Insert from .env"),
        Binding("up", "focus_previous", "Prev", show=False),
        Binding("down", "focus_next", "Next", show=False),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self, state: AppState, var: VarToResolve, env_vars: dict[str, str]
    ) -> None:
        super().__init__()
        self.state = state
        self.var = var
        self.env_vars = env_vars
        # Seed the editor with the current resolution if the user has
        # already touched this var, otherwise the compose default if any,
        # else empty.
        if var.has_resolution:
            self._initial = var.resolved or ""
        elif var.default is not None:
            self._initial = var.default
        else:
            self._initial = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="vol-edit"):
            yield Static(
                f"[b]Resolve ${{{self.var.name}}}[/b]\n"
                f"used in: {self.var.usage_summary}\n"
                + (
                    f"compose default: {self.var.default}\n"
                    if self.var.has_default
                    else ""
                )
            )
            yield Label("Replacement value:")
            yield Input(value=self._initial, id="var-input")
            with Horizontal(classes="vol-row"):
                yield Button("Insert bind (Ctrl+B)", id="insert-bind-btn")
                yield Button("Insert vault (Ctrl+V)", id="insert-vault-btn")
                yield Button("New vault key (Ctrl+N)", id="new-vault-btn")
                if self.env_vars:
                    yield Button("Insert from .env (Ctrl+E)", id="insert-env-btn")
                yield Button("Save (Ctrl+S)", id="save-btn", variant="primary")
                yield Button("Cancel (Esc)", id="cancel-btn")
        yield Footer()

    def on_mount(self) -> None:
        inp: Input = self.query_one("#var-input", Input)
        inp.focus()
        # Drop the cursor at the end so user can append, not overwrite.
        inp.cursor_position = len(self._initial)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "save-btn": self.action_save,
            "cancel-btn": self.action_cancel,
            "insert-bind-btn": self.action_insert_bind,
            "insert-vault-btn": self.action_insert_vault,
            "new-vault-btn": self.action_create_vault,
            "insert-env-btn": self.action_insert_env,
        }
        handler = actions.get(event.button.id or "")
        if handler:
            handler()

    def action_save(self) -> None:
        value = self.query_one("#var-input", Input).value
        if not value and not self.var.has_default:
            self.notify(
                "Empty value with no compose default — variable would stay unresolved. "
                "Cancel instead if that's what you want.",
                severity="warning",
                timeout=4,
            )
            return
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    # ---- inserters ---------------------------------------------------

    def action_insert_bind(self) -> None:
        from . import _inserters

        _inserters.insert_bind_var(self, self.state, "var-input")

    def action_insert_vault(self) -> None:
        from . import _inserters

        _inserters.insert_vault_ref(self, self.state, "var-input")

    def action_create_vault(self) -> None:
        from . import _inserters

        _inserters.create_vault_key(
            self, self.state, "var-input", self._suggest_vault_key(self.var.name)
        )

    def action_insert_env(self) -> None:
        from . import _inserters

        _inserters.insert_env_value(self, self.state, "var-input", self.env_vars)

    @staticmethod
    def _suggest_vault_key(var_name: str) -> str:
        # Re-exported here so existing tests that pin the helper to the
        # screen class keep passing — see :func:`_inserters.suggest_vault_key`.
        from . import _inserters

        return _inserters.suggest_vault_key(var_name)
