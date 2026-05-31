"""Reusable insertion helpers for catalog/vault/env splicing.

Several editors in the app present the same three+ insertion shortcuts on
a single :class:`textual.widgets.Input`:

* ``Ctrl+B`` — splice ``{{ docker_bind_catalog.<key>.mnt }}``
* ``Ctrl+V`` — splice ``{{ vault_<name> }}`` from existing keys
* ``Ctrl+N`` — create a new vault key inline and splice the reference
* ``Ctrl+E`` — pick from a discovered ``.env`` mapping (full replacement)

Rather than duplicating the picker/vault plumbing in every editor we
expose helper functions here. Editors keep their own bindings and
buttons but delegate the work.

Inputs to every helper:

* ``screen`` — the editor screen (used to push child screens and notify)
* ``state`` — the global :class:`AppState`
* ``input_id`` — the DOM id of the :class:`Input` to splice into
"""

from __future__ import annotations

from typing import Mapping

from textual.widgets import Input


def splice_at_cursor(screen, input_id: str, snippet: str) -> None:  # noqa: ANN001
    inp: Input = screen.query_one(f"#{input_id}", Input)
    text = inp.value
    pos = inp.cursor_position
    inp.value = text[:pos] + snippet + text[pos:]
    inp.cursor_position = pos + len(snippet)
    inp.focus()


def replace_value(screen, input_id: str, value: str) -> None:  # noqa: ANN001
    inp: Input = screen.query_one(f"#{input_id}", Input)
    inp.value = value
    inp.cursor_position = len(value)
    inp.focus()


def insert_bind_var(screen, state, input_id: str) -> None:  # noqa: ANN001
    """Open a picker over ``docker_bind_catalog`` and splice on selection."""
    from .catalog_picker import SinglePickerScreen

    bind_cat = state.bind_catalog()
    if not bind_cat:
        screen.notify("No binds defined yet", severity="warning")
        return

    def _on_pick(key: str | None) -> None:
        if key is None:
            return
        splice_at_cursor(
            screen, input_id, "{{ docker_bind_catalog." + key + ".mnt }}"
        )

    screen.app.push_screen(
        SinglePickerScreen(
            "Insert bind variable at cursor",
            bind_cat,
            None,
            describe=lambda k, v: f"src={v.get('src')}, mnt={v.get('mnt')}"
            if isinstance(v, dict)
            else "",
        ),
        _on_pick,
    )


def insert_vault_ref(screen, state, input_id: str) -> None:  # noqa: ANN001
    """Pick an existing vault key and splice ``{{ vault_<name> }}``.

    The plaintext value never leaves the vault — the helper inserts a
    Jinja reference that Ansible resolves at play time.
    """
    from .catalog_picker import SinglePickerScreen
    from .secrets import _VaultPasswordPrompt
    from .. import vault as vault_mod

    def _continue() -> None:
        try:
            secrets = vault_mod.load_vault_yaml(
                state.vault_path, state.vault_config
            )
        except vault_mod.VaultError as exc:
            screen.notify(str(exc), severity="error", timeout=6)
            return
        if not secrets:
            screen.notify(
                "Vault is empty — use 'New vault key' (Ctrl+N) to add one",
                severity="warning",
            )
            return
        entries = {k: None for k in sorted(secrets.keys())}

        def _on_pick(name: str | None) -> None:
            if name:
                splice_at_cursor(screen, input_id, "{{ " + name + " }}")

        screen.app.push_screen(
            SinglePickerScreen(
                "Insert vault secret at cursor",
                entries,
                None,
                describe=lambda _k, _v: "(reference only — value stays in vault)",
            ),
            _on_pick,
        )

    if state.vault_config is None:

        def _after_pw(result: object) -> None:
            if result is None:
                return
            state.vault_config = result  # type: ignore[assignment]
            _continue()

        screen.app.push_screen(
            _VaultPasswordPrompt(state, vault_path=state.vault_path),
            _after_pw,
        )
        return
    _continue()


def create_vault_key(screen, state, input_id: str, suggested_key: str) -> None:  # noqa: ANN001
    """Open the secret editor with a suggested (editable) key, save into
    the vault, then splice the reference into the input.
    """
    from .secrets import _SecretEditorScreen, _VaultPasswordPrompt
    from .. import vault as vault_mod

    def _continue() -> None:
        try:
            existing = vault_mod.load_vault_yaml(
                state.vault_path, state.vault_config
            )
        except vault_mod.VaultError as exc:
            screen.notify(str(exc), severity="error", timeout=6)
            return

        def _on_save(result: tuple[str, str] | None) -> None:
            if result is None:
                return
            key, value = result
            if not key:
                screen.notify("Key cannot be empty", severity="error")
                return
            if key in existing:
                screen.notify(
                    f"Key {key} already exists — pick 'Insert vault' instead",
                    severity="warning",
                )
                return
            existing[key] = value
            try:
                vault_mod.save_vault_yaml(
                    state.vault_path, existing, state.vault_config
                )
            except vault_mod.VaultError as exc:
                screen.notify(f"Save failed: {exc}", severity="error", timeout=6)
                return
            screen.notify(f"Created {key} in vault", timeout=2)
            splice_at_cursor(screen, input_id, "{{ " + key + " }}")

        screen.app.push_screen(
            _SecretEditorScreen(
                initial_key=suggested_key, initial_value="", is_new=True
            ),
            _on_save,
        )

    if state.vault_config is None:

        def _after_pw(result: object) -> None:
            if result is None:
                return
            state.vault_config = result  # type: ignore[assignment]
            _continue()

        screen.app.push_screen(
            _VaultPasswordPrompt(state, vault_path=state.vault_path),
            _after_pw,
        )
        return
    _continue()


def insert_env_value(
    screen,  # noqa: ANN001
    state,  # noqa: ANN001  (kept for symmetry; not currently used)
    input_id: str,
    env_vars: Mapping[str, str],
) -> None:
    """Pick a value from a ``.env`` mapping and *replace* the input.

    Replacement (rather than splice) is intentional — picking from a
    ``.env`` is conceptually "use this value", not "embed it". For inline
    splicing the user can just type the value or use vault.
    """
    from .catalog_picker import SinglePickerScreen

    if not env_vars:
        screen.notify("No .env file detected", severity="warning")
        return

    def _on_pick(key: str | None) -> None:
        if key is None:
            return
        replace_value(screen, input_id, env_vars.get(key, ""))

    screen.app.push_screen(
        SinglePickerScreen(
            "Pick value from .env",
            dict(env_vars),
            None,
            describe=lambda _k, v: _truncate(str(v)),
        ),
        _on_pick,
    )


def suggest_vault_key(var_name: str) -> str:
    """``POSTGRES_PASSWORD`` → ``vault_postgres_password``; idempotent."""
    stem = var_name.lower()
    if stem.startswith("vault_"):
        stem = stem[len("vault_"):]
    return f"vault_{stem}"


def _truncate(value: str, limit: int = 60) -> str:
    return value if len(value) <= limit else value[: limit - 1] + "…"
