"""Create a new host: hostname + IP → groups → drop into the host editor.

Three steps so the user is never confronted with a giant form:

1. :class:`_HostBasicsScreen` — hostname and IP. Validates both before
   moving on; an empty hostname is rejected, and we warn (but allow) IP
   strings that don't look like a v4 address since the user might be
   typing a hostname intentionally.
2. :class:`_HostGroupsScreen` — multi-pick from assignable groups. We
   re-use :class:`MultiPickerScreen` rather than rolling another widget.
3. After both succeed, the inventory and host_vars files are updated and
   the existing :class:`HostEditorScreen` is opened so the user can
   continue with catalog selection — the same flow as editing any host.
"""

from __future__ import annotations

import ipaddress

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, Static

from .. import yaml_io
from ..hostvars import HostVars
from ..inventory import list_assignable_groups
from ..state import AppState
from .catalog_picker import MultiPickerScreen


class NewHostScreen(ModalScreen[str | None]):
    """Top-level driver for the multi-step new-host flow.

    Returns the new host's name on success (so the caller can navigate to
    its editor), or None on cancel.
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state

    def on_mount(self) -> None:
        # Drive the flow from on_mount so we don't need a compose layout
        # of our own — this screen acts purely as a coordinator.
        self.app.push_screen(_HostBasicsScreen(self.state), self._on_basics_done)

    def compose(self) -> ComposeResult:
        # A nearly-empty backdrop while the sub-screens drive the flow.
        with Vertical(id="new-host-shell"):
            yield Static("[dim]Creating new host…[/dim]")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _on_basics_done(self, basics: tuple[str, str] | None) -> None:
        if basics is None:
            self.dismiss(None)
            return
        host, ip = basics
        if host in self.state.inventory.hosts():
            self.notify(
                f"Host '{host}' already exists in inventory", severity="error"
            )
            self.dismiss(None)
            return
        # Step 2: pick groups.
        all_groups = list_assignable_groups(self.state.inventory)
        entries = {g: None for g in all_groups}

        def _on_groups(selected: list[str] | None) -> None:
            if selected is None:
                self.dismiss(None)
                return
            self._commit(host, ip, selected)

        self.app.push_screen(
            MultiPickerScreen(
                f"Groups for {host}", entries, [], describe=lambda _k, _v: ""
            ),
            _on_groups,
        )

    def _commit(self, host: str, ip: str, groups: list[str]) -> None:
        """Write the inventory and host_vars entries, then open the editor."""
        inv = self.state.inventory
        # Add the host to each chosen group. Even if the user picked no
        # groups we still create the host so they can get to the editor and
        # add groups later.
        for g in groups:
            inv.add_host_to_group(host, g)
        inv.save()

        # Seed host_vars with at least ``ansible_host``. Other catalog
        # selections are made afterwards from the host editor.
        hv = HostVars.load(self.state.layout.host_vars_dir, host)
        hv.set("ansible_host", ip)
        hv.save()
        # Cache the new host_vars so the editor sees it without a re-read.
        self.state.host_vars_cache[host] = hv

        self.notify(f"Created {host} ({ip})", timeout=2)

        # Hand off to the existing host editor so the user can fill in
        # catalog selections immediately. Pop the coordinator first so the
        # editor's Esc-back lands the user on the host list.
        from .host_editor import HostEditorScreen

        self.dismiss(host)
        self.app.push_screen(HostEditorScreen(self.state, host))


class _HostBasicsScreen(ModalScreen[tuple[str, str] | None]):
    """Step 1: hostname + IP."""

    BINDINGS = [
        Binding("ctrl+s", "save", "Continue"),
        Binding("up", "focus_previous", "Prev", show=False),
        Binding("down", "focus_next", "Next", show=False),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="bind-choice"):
            yield Static(
                "[b]New host: basics[/b]\n"
                "Step 1 of 2 — pick a hostname and management IP.",
            )
            yield Label("Hostname (matches an entry in hosts.yml):")
            yield Input(placeholder="e.g. port-essos", id="host-input")
            yield Label("Management IP (used as ansible_host):")
            yield Input(placeholder="e.g. 192.168.1.150", id="ip-input")
            yield Static("", id="error-msg", classes="dim")
            with Horizontal(classes="vol-row"):
                yield Button("Continue (Ctrl+S)", id="next-btn", variant="primary")
                yield Button("Cancel (Esc)", id="cancel-btn")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#host-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "next-btn":
            self.action_save()
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def action_save(self) -> None:
        host = self.query_one("#host-input", Input).value.strip()
        ip = self.query_one("#ip-input", Input).value.strip()
        err = self._validate(host, ip)
        if err is not None:
            self.query_one("#error-msg", Static).update(f"[red]{err}[/red]")
            return
        self.dismiss((host, ip))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _validate(self, host: str, ip: str) -> str | None:
        if not host:
            return "Hostname can't be empty"
        if any(c.isspace() for c in host):
            return "Hostname can't contain whitespace"
        if not ip:
            return "Management IP can't be empty"
        # We don't *require* a valid v4 address — some hosts use DNS names
        # for ansible_host — but flag obvious typos.
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            # Heuristic: dotted decimal-ish but malformed → reject.
            if ip.count(".") == 3 and all(p.isdigit() for p in ip.split(".")):
                return f"'{ip}' is not a valid IP address"
            # Otherwise assume it's a hostname/DNS name and accept it.
        return None
