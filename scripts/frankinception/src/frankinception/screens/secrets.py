"""Manage Ansible Vault secrets through the TUI.

Three screens:

* :class:`SecretsScreen` — list every key in the vault with redacted values
  and offer add / edit / delete / migrate.
* :class:`_SecretEditorScreen` — multi-line editor for a single secret. Used
  for both new and existing keys.
* :class:`_VaultPasswordPrompt` — first-launch password prompt when no
  password file is configured. Optionally writes the password to ``~/.vault_pass``
  and updates ``ansible.cfg`` so future sessions don't have to ask.

Secrets are stored as a single vault-encrypted YAML file (typically
``group_vars/all/vault.yml``) holding a flat mapping of ``vault_<name> -> value``.
The ``vault_`` prefix is convention so plays can tell at a glance which vars
are sensitive (the project's ``secrets_catalog.yml`` references them as
``"{{ vault_robot_pub }}"`` rather than ``lookup('file', ...)``).
"""

from __future__ import annotations

from pathlib import Path

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

from .. import vault as vault_mod
from ..state import AppState


class SecretsScreen(Screen):
    """Top-level secrets management screen."""

    BINDINGS = [
        Binding("a", "add", "Add"),
        Binding("e", "edit", "Edit"),
        Binding("d", "delete", "Delete"),
        Binding("m", "migrate", "Migrate plaintext"),
        Binding("r", "reload", "Reload"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self._secrets: dict[str, str] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(self._heading(), id="secrets-heading")
            with Vertical(classes="pane"):
                yield DataTable(
                    id="secrets-table", cursor_type="row", zebra_stripes=True
                )
            with Horizontal(id="secrets-actions"):
                yield Button("[u]A[/u]dd", id="add-btn", variant="primary")
                yield Button("[u]E[/u]dit", id="edit-btn")
                yield Button("[u]D[/u]elete", id="delete-btn")
                yield Button("[u]M[/u]igrate plaintext file", id="migrate-btn")
        yield Footer()

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#secrets-table", DataTable)
        table.add_columns("Key", "Length", "Preview")
        self._ensure_password(self._initial_load)

    # ---- password plumbing ------------------------------------------

    def _ensure_password(self, on_ready: callable) -> None:
        """Run ``on_ready`` once a usable VaultConfig is available."""
        if self.state.vault_config is not None:
            on_ready()
            return

        def _on_done(cfg: object) -> None:
            if cfg is None:
                self.app.pop_screen()
                return
            self.state.vault_config = cfg  # type: ignore[assignment]
            on_ready()

        self.app.push_screen(
            _VaultPasswordPrompt(self.state, vault_path=self.state.vault_path),
            _on_done,
        )

    # ---- vault load/save --------------------------------------------

    def _initial_load(self) -> None:
        try:
            self._secrets = vault_mod.load_vault_yaml(
                self.state.vault_path, self.state.vault_config  # type: ignore[arg-type]
            )
        except vault_mod.VaultError as exc:
            self.notify(str(exc), severity="error", timeout=8)
            self._secrets = {}
        self._refill()

    def _save(self) -> bool:
        try:
            vault_mod.save_vault_yaml(
                self.state.vault_path,
                self._secrets,
                self.state.vault_config,  # type: ignore[arg-type]
            )
            return True
        except vault_mod.VaultError as exc:
            self.notify(f"Save failed: {exc}", severity="error", timeout=8)
            return False

    def _refill(self) -> None:
        table: DataTable = self.query_one("#secrets-table", DataTable)
        table.clear()
        for key in sorted(self._secrets.keys()):
            value = self._secrets[key]
            length = len(str(value))
            preview = _preview(str(value))
            table.add_row(key, str(length), preview, key=key)

    # ---- actions -----------------------------------------------------

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_reload(self) -> None:
        self._initial_load()
        self.notify("Reloaded vault", timeout=2)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "add-btn": self.action_add,
            "edit-btn": self.action_edit,
            "delete-btn": self.action_delete,
            "migrate-btn": self.action_migrate,
        }
        handler = mapping.get(event.button.id or "")
        if handler:
            handler()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is not None:
            self._open_editor(str(event.row_key.value))

    def action_add(self) -> None:
        self._open_editor(None)

    def action_edit(self) -> None:
        key = self._cursor_key()
        if key is None:
            self.notify("Pick a row first", severity="warning")
            return
        self._open_editor(key)

    def action_delete(self) -> None:
        key = self._cursor_key()
        if key is None:
            self.notify("Pick a row first", severity="warning")
            return

        def _on_confirm(yes: bool | None) -> None:
            if yes:
                self._secrets.pop(key, None)
                if self._save():
                    self._refill()
                    self.notify(f"Deleted {key}", timeout=2)

        self.app.push_screen(_DeleteConfirm(key), _on_confirm)

    def action_migrate(self) -> None:
        self.app.push_screen(
            _MigrateScreen(),
            self._on_migrate_done,
        )

    def _on_migrate_done(self, result: tuple[str, str] | None) -> None:
        if result is None:
            return
        key, value = result
        if key in self._secrets:
            self.notify(
                f"Key {key} already exists — edit it directly to merge",
                severity="warning",
            )
            return
        self._secrets[key] = value
        if self._save():
            self._refill()
            self.notify(f"Imported {key}", timeout=2)

    # ---- helpers -----------------------------------------------------

    def _heading(self) -> str:
        return (
            f"[b]Secrets[/b]   vault: [cyan]{self.state.vault_path}[/cyan]\n"
            "Enter on a row to edit. a/e/d/m for actions, Esc to back."
        )

    def _cursor_key(self) -> str | None:
        table: DataTable = self.query_one("#secrets-table", DataTable)
        if table.row_count == 0:
            return None
        try:
            row_key = table.ordered_rows[table.cursor_row].key
        except (AttributeError, IndexError):
            return None
        return str(row_key.value) if row_key.value is not None else None

    def _open_editor(self, key: str | None) -> None:
        existing = self._secrets.get(key) if key else None
        self.app.push_screen(
            _SecretEditorScreen(initial_key=key, initial_value=existing or ""),
            self._on_editor_done,
        )

    def _on_editor_done(self, result: tuple[str, str] | None) -> None:
        if result is None:
            return
        new_key, new_value = result
        if not new_key:
            self.notify("Key cannot be empty", severity="error")
            return
        self._secrets[new_key] = new_value
        if self._save():
            self._refill()
            self.notify(f"Saved {new_key}", timeout=2)


def _preview(value: str, limit: int = 50) -> str:
    """One-line redacted preview that hints at content type."""
    first_line = value.splitlines()[0] if value else ""
    if first_line.startswith("ssh-") or first_line.startswith("ecdsa-"):
        # SSH public keys are not really secret — show the type and
        # comment so the user can spot the right one at a glance.
        parts = first_line.split()
        if len(parts) >= 1:
            label = parts[0]
            comment = parts[2] if len(parts) >= 3 else ""
            return f"{label} … {comment}".strip()
    if first_line.startswith("-----BEGIN"):
        return first_line  # PEM banner is structural, not secret
    redacted = "•" * min(len(value), 12)
    return redacted or "(empty)"


# ---- secret editor ----------------------------------------------------------


class _SecretEditorScreen(ModalScreen[tuple[str, str] | None]):
    """Edit a single secret. Multiline value editor."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("up", "focus_previous", "Prev", show=False),
        Binding("down", "focus_next", "Next", show=False),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        initial_key: str | None,
        initial_value: str,
        *,
        is_new: bool | None = None,
    ) -> None:
        super().__init__()
        self._initial_key = initial_key or ""
        self._initial_value = initial_value
        # ``is_new`` lets callers distinguish "new with a suggested key"
        # (editable) from "edit existing key" (locked) without overloading
        # the value of ``initial_key``. When None, fall back to the old
        # implicit rule.
        self._is_new = is_new if is_new is not None else initial_key is None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="secret-edit"):
            mode = "New secret" if self._is_new else f"Edit {self._initial_key}"
            yield Static(f"[b]{mode}[/b]")
            yield Label("Key (e.g. vault_robot_pub):")
            yield Input(
                value=self._initial_key, id="secret-key", disabled=not self._is_new
            )
            yield Label("Value (multi-line; use Tab to leave the editor):")
            yield TextArea(self._initial_value, id="secret-value", soft_wrap=True)
            with Horizontal(classes="vol-row"):
                yield Button("Save (Ctrl+S)", id="save-btn", variant="primary")
                yield Button("Cancel (Esc)", id="cancel-btn")
        yield Footer()

    def on_mount(self) -> None:
        # For new secrets with a suggested key, land on the key field so
        # the user can tweak it. For edits (key locked), land on the value
        # editor — the key field is non-interactive there anyway.
        if self._is_new and self._initial_key:
            inp: Input = self.query_one("#secret-key", Input)
            inp.focus()
            inp.cursor_position = len(self._initial_key)
        else:
            self.query_one("#secret-value", TextArea).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-btn":
            self.action_save()
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def action_save(self) -> None:
        key = self.query_one("#secret-key", Input).value.strip()
        value = self.query_one("#secret-value", TextArea).text
        if not key:
            self.notify("Key cannot be empty", severity="error")
            return
        # Strip a single trailing newline (almost always picked up from the
        # input file) but preserve internal newlines.
        if value.endswith("\n"):
            value = value[:-1]
        self.dismiss((key, value))

    def action_cancel(self) -> None:
        self.dismiss(None)


# ---- delete confirm ---------------------------------------------------------


class _DeleteConfirm(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "yes", "Delete"),
        Binding("n", "no", "Cancel"),
        Binding("escape", "no", "Cancel", show=False),
    ]

    def __init__(self, key: str) -> None:
        super().__init__()
        self._key = key

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="picker"):
            yield Static(
                f"[b]Delete {self._key}?[/b]\n\n"
                "This cannot be undone (other than restoring from VCS or backup).",
                id="picker-title",
            )
            yield Static("y to delete · n or Esc to cancel", id="picker-help")
            yield Button("Delete (y)", id="delete-btn", variant="error")
            yield Button("Cancel (n)", id="cancel-btn", variant="primary")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "delete-btn":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


# ---- vault password setup ---------------------------------------------------


class _VaultPasswordPrompt(ModalScreen[object]):
    """First-launch prompt for the vault password.

    The password is held in process memory only — never written to disk —
    so the user has to type it once per session. This is intentional: any
    on-disk password file would let anyone with read access to the C2
    server's filesystem decrypt the vault.

    Returns a :class:`vault_mod.VaultConfig` on success, or ``None`` on cancel.
    """

    BINDINGS = [
        Binding("up", "focus_previous", "Prev", show=False),
        Binding("down", "focus_next", "Next", show=False),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, state: AppState, vault_path: Path) -> None:
        super().__init__()
        self.state = state
        self.vault_path = vault_path

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="bind-choice"):
            yield Static(
                "[b]Vault password[/b]\n"
                f"vault file: {self.vault_path}\n\n"
                "The password is held in memory for this session only "
                "and is not written to disk.",
                id="bind-info",
            )
            yield Label("Password (won't echo):")
            yield Input(password=True, id="vault-pw")
            with Horizontal(classes="vol-row"):
                yield Button("Use", id="use-btn", variant="primary")
                yield Button("Cancel", id="cancel-btn")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#vault-pw", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "use-btn":
            self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        # Pressing Enter inside the password field submits.
        if event.input.id == "vault-pw":
            self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _submit(self) -> None:
        pw = self.query_one("#vault-pw", Input).value
        if not pw:
            self.notify("Password is empty", severity="error")
            return
        self.dismiss(vault_mod.VaultConfig(password=pw))


# ---- migrate plaintext file -------------------------------------------------


class _MigrateScreen(ModalScreen[tuple[str, str] | None]):
    """Read a plaintext secret file (e.g. ``~/secrets/robot.pub``) into a
    new vault entry, with a suggested key derived from the filename.
    """

    BINDINGS = [
        Binding("up", "focus_previous", "Prev", show=False),
        Binding("down", "focus_next", "Next", show=False),
        Binding("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="bind-choice"):
            yield Static(
                "[b]Migrate plaintext file into vault[/b]\n"
                "Reads the file, suggests a key like ``vault_<basename>``, and "
                "lets you tweak it before saving."
            )
            yield Label("Plaintext file path:")
            yield Input(placeholder="~/secrets/robot.pub", id="path-input")
            yield Label("Vault key (auto-suggested when the path loads):")
            yield Input(id="key-input")
            yield Label("Preview (first 80 chars):")
            yield Static("(no file loaded)", id="preview")
            with Horizontal(classes="vol-row"):
                yield Button("Load", id="load-btn")
                yield Button("Save to vault", id="save-btn", variant="primary")
                yield Button("Cancel", id="cancel-btn")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#path-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "load-btn":
            self._load()
        elif event.button.id == "save-btn":
            self._save()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _load(self) -> None:
        path_str = self.query_one("#path-input", Input).value.strip()
        if not path_str:
            self.notify("Enter a path first", severity="warning")
            return
        path = Path(path_str).expanduser()
        if not path.is_file():
            self.notify(f"File not found: {path}", severity="error")
            return
        try:
            self._loaded_value = path.read_text(encoding="utf-8")
        except OSError as exc:
            self.notify(f"Read failed: {exc}", severity="error")
            return
        suggested = "vault_" + path.name.lower().replace(".", "_").replace("-", "_")
        key_input: Input = self.query_one("#key-input", Input)
        if not key_input.value.strip():
            key_input.value = suggested
        preview = self._loaded_value.splitlines()[0] if self._loaded_value else ""
        if len(preview) > 80:
            preview = preview[:77] + "…"
        self.query_one("#preview", Static).update(preview or "(empty file)")
        self.notify(f"Loaded {path}", timeout=2)

    def _save(self) -> None:
        if not getattr(self, "_loaded_value", None):
            self.notify("Load a file first", severity="warning")
            return
        key = self.query_one("#key-input", Input).value.strip()
        if not key:
            self.notify("Key cannot be empty", severity="error")
            return
        value = self._loaded_value
        # Strip the trailing newline that nearly every file has.
        if value.endswith("\n"):
            value = value[:-1]
        self.dismiss((key, value))
