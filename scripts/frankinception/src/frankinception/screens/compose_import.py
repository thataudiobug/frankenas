"""Import docker-compose / docker run into ``docker_containers_catalog``.

Flow:
1. User pastes text or types a file path.
2. We parse, then walk every volume against ``docker_bind_catalog``.
3. For each unmatched volume we open a sub-screen letting the user either:
   * pick an existing bind whose ``mnt`` we'll splice in,
   * define a new bind (writes to ``mounts_catalog.yml``), or
   * leave the path verbatim.
4. Show the resulting catalog YAML diff and confirm the write.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Header, Input, Label, Static, TextArea

from .. import yaml_io
from ..bind_mapper import VolumeMatch, add_bind, match_volume, split_volume
from ..compose_parser import ParsedContainer, parse_any, parse_compose, parse_file
from ..state import AppState


class ComposeImportScreen(Screen):
    BINDINGS = [
        Binding("ctrl+s", "parse", "Parse"),
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(
                "[b]Import docker-compose / docker run[/b]\n"
                "Paste a compose file or one or more 'docker run' lines. "
                "Or enter a file path. Ctrl+S to parse.",
                id="import-help",
            )
            with Horizontal(id="import-row"):
                yield Input(placeholder="optional: path to compose file", id="path-input")
                yield Button("Load file", id="load-btn")
            yield TextArea(id="paste-area", language="yaml")
            yield Button("Parse and continue (Ctrl+S)", id="parse-btn", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#paste-area", TextArea).focus()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "load-btn":
            self._load_file()
        elif event.button.id == "parse-btn":
            self.action_parse()

    def _load_file(self) -> None:
        path_str = self.query_one("#path-input", Input).value.strip()
        if not path_str:
            self.notify("Enter a path first", severity="warning")
            return
        path = Path(path_str).expanduser()
        if not path.is_file():
            self.notify(f"File not found: {path}", severity="error")
            return
        self.query_one("#paste-area", TextArea).text = path.read_text(encoding="utf-8")
        self.notify(f"Loaded {path}")

    def action_parse(self) -> None:
        text = self.query_one("#paste-area", TextArea).text
        try:
            containers = parse_any(text)
        except Exception as exc:  # noqa: BLE001
            self.notify(f"Parse failed: {exc}", severity="error", timeout=5)
            return
        if not containers:
            self.notify("Nothing to import", severity="warning")
            return
        self.app.push_screen(_VolumeMappingScreen(self.state, containers))


class _VolumeMappingScreen(Screen):
    """Walk through unmatched volumes, then preview and write."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, state: AppState, containers: list[ParsedContainer]) -> None:
        super().__init__()
        self.state = state
        self.containers = containers
        # Per-container, list of (raw_volume, current_mapping) pairs.
        self.mappings: dict[str, list[tuple[str, VolumeMatch]]] = {}
        bind_cat = self.state.bind_catalog()
        for c in containers:
            entries: list[tuple[str, VolumeMatch]] = []
            for vol in c.volumes:
                entries.append((vol, match_volume(vol, bind_cat)))
            self.mappings[c.name] = entries

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(
                "[b]Volume mapping[/b]\n"
                "Volumes that didn't match docker_bind_catalog are listed below. "
                "Select one to map. Press Enter on '[done]' when finished.",
                id="map-help",
            )
            yield Static(self._summary(), id="map-summary")
            yield Button("Continue to preview", id="continue-btn", variant="primary")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "continue-btn":
            self._handle_unmatched_or_preview()

    def _handle_unmatched_or_preview(self) -> None:
        for cname, entries in self.mappings.items():
            for idx, (raw, m) in enumerate(entries):
                if m.needs_user_choice:
                    self.app.push_screen(
                        _BindChoiceScreen(self.state, cname, idx, raw, m),
                        self._on_choice,
                    )
                    return
        # All volumes resolved — show preview.
        self.app.push_screen(_PreviewScreen(self.state, self.containers, self.mappings))

    def _on_choice(self, result: tuple[str, int, VolumeMatch] | None) -> None:
        if result is None:
            self.notify("Mapping cancelled — try again or Esc to abandon", severity="warning")
            return
        cname, idx, new_match = result
        raw, _ = self.mappings[cname][idx]
        self.mappings[cname][idx] = (raw, new_match)
        self.query_one("#map-summary", Static).update(self._summary())
        # Continue with the next unmatched.
        self._handle_unmatched_or_preview()

    def _summary(self) -> str:
        lines: list[str] = []
        for c in self.containers:
            lines.append(f"[b]{c.name}[/b]  ({c.image})")
            entries = self.mappings.get(c.name, [])
            if not entries:
                lines.append("  (no volumes)")
                continue
            for raw, m in entries:
                if m.needs_user_choice:
                    lines.append(f"  [red]?[/red] {raw}    [dim]→ unmapped[/dim]")
                elif m.bind_key:
                    lines.append(
                        f"  [green]✓[/green] {raw}    [dim]→ {m.rendered}[/dim]"
                    )
                else:
                    lines.append(f"  [yellow]·[/yellow] {raw}    [dim]→ kept verbatim[/dim]")
        return "\n".join(lines)


class _BindChoiceScreen(ModalScreen[tuple[str, int, VolumeMatch] | None]):
    """Decide what to do with a single unmatched volume."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        state: AppState,
        container: str,
        idx: int,
        raw_volume: str,
        match: VolumeMatch,
    ) -> None:
        super().__init__()
        self.state = state
        self.container = container
        self.idx = idx
        self.raw_volume = raw_volume
        self.match = match

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="bind-choice"):
            yield Static(
                f"[b]Unmapped volume[/b]\n"
                f"container: {self.container}\n"
                f"volume:    {self.raw_volume}\n"
                f"host path: {self.match.raw_host_path}",
                id="bind-info",
            )
            yield Label("Pick an action:")
            yield Button("Use existing bind…", id="existing-btn")
            yield Button("Define a new bind…", id="new-btn", variant="primary")
            yield Button("Keep verbatim (no rewrite)", id="keep-btn")
            yield Button("Cancel", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "existing-btn":
            self._pick_existing()
        elif event.button.id == "new-btn":
            self._define_new()
        elif event.button.id == "keep-btn":
            verbatim = VolumeMatch(
                rendered=self.raw_volume,
                bind_key=None,
                raw_host_path=self.match.raw_host_path,
                raw_container_path=self.match.raw_container_path,
                mode=self.match.mode,
                needs_user_choice=False,
            )
            self.dismiss((self.container, self.idx, verbatim))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _pick_existing(self) -> None:
        from .catalog_picker import SinglePickerScreen

        bind_cat = self.state.bind_catalog()

        def _on_pick(key: str | None) -> None:
            if key is None:
                return
            entry = bind_cat[key]
            mnt = entry["mnt"] if isinstance(entry, dict) else None
            if not mnt:
                self.notify("Selected bind has no mnt", severity="error")
                return
            host_path = self.match.raw_host_path
            # Treat the entire host path as living "under" the chosen bind:
            # use the basename suffix only if the host path lives under the
            # bind's src; otherwise keep the host path basename appended.
            src = entry.get("src", "").rstrip("/")
            if host_path.startswith(src + "/"):
                suffix = host_path[len(src):]
            elif host_path == src:
                suffix = ""
            else:
                suffix = "/" + host_path.lstrip("/").split("/", 1)[-1]
            rewritten = "{{ docker_bind_catalog." + key + ".mnt }}" + suffix
            rendered = f"{rewritten}:{self.match.raw_container_path}"
            if self.match.mode:
                rendered += f":{self.match.mode}"
            new_match = VolumeMatch(
                rendered=rendered,
                bind_key=key,
                raw_host_path=self.match.raw_host_path,
                raw_container_path=self.match.raw_container_path,
                mode=self.match.mode,
                needs_user_choice=False,
            )
            self.dismiss((self.container, self.idx, new_match))

        self.app.push_screen(
            SinglePickerScreen(
                "Pick existing bind",
                bind_cat,
                None,
                describe=lambda k, v: f"src={v.get('src')}, mnt={v.get('mnt')}"
                if isinstance(v, dict)
                else "",
            ),
            _on_pick,
        )

    def _define_new(self) -> None:
        self.app.push_screen(
            _NewBindScreen(self.state, self.match.raw_host_path),
            self._on_new_bind,
        )

    def _on_new_bind(self, result: tuple[str, str, str] | None) -> None:
        if result is None:
            return
        key, src, mnt = result
        bind_cat = self.state.bind_catalog()
        add_bind(bind_cat, key, src, mnt)
        self.state.save_bind_catalog()
        # Re-match using the updated catalog.
        new_match = match_volume(self.raw_volume, bind_cat)
        self.dismiss((self.container, self.idx, new_match))


class _NewBindScreen(ModalScreen[tuple[str, str, str] | None]):
    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, state: AppState, host_path_hint: str) -> None:
        super().__init__()
        self.state = state
        # Suggest a key from the host path basename.
        self._suggested_key = (
            Path(host_path_hint).name.lower().replace(" ", "_") or "bind"
        )
        # Default src to the first existing parent directory.
        self._suggested_src = host_path_hint
        self._suggested_mnt = "/mnt/" + self._suggested_key

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="new-bind"):
            yield Static("[b]Define new docker_bind_catalog entry[/b]")
            yield Label("Key (used as docker_bind_catalog.<key>):")
            yield Input(value=self._suggested_key, id="key-input")
            yield Label("Host source path (src):")
            yield Input(value=self._suggested_src, id="src-input")
            yield Label("In-container mount prefix (mnt):")
            yield Input(value=self._suggested_mnt, id="mnt-input")
            yield Button("Add", id="add-btn", variant="primary")
            yield Button("Cancel", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)
            return
        if event.button.id != "add-btn":
            return
        key = self.query_one("#key-input", Input).value.strip()
        src = self.query_one("#src-input", Input).value.strip()
        mnt = self.query_one("#mnt-input", Input).value.strip()
        if not (key and src and mnt):
            self.notify("All three fields required", severity="error")
            return
        if key in self.state.bind_catalog():
            self.notify(f"Bind '{key}' already exists", severity="error")
            return
        self.dismiss((key, src, mnt))

    def action_cancel(self) -> None:
        self.dismiss(None)


class _PreviewScreen(Screen):
    """Show the YAML that will be merged into the docker catalog and confirm."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
    ]

    def __init__(
        self,
        state: AppState,
        containers: list[ParsedContainer],
        mappings: dict[str, list[tuple[str, Any]]],
    ) -> None:
        super().__init__()
        self.state = state
        self.containers = containers
        self.mappings = mappings

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(
                f"[b]Preview[/b] — these entries will be added/updated in:\n"
                f"  {self.state.docker_catalog_path}\n"
                "Existing keys with the same name will be overwritten.",
                id="preview-heading",
            )
            yield TextArea(
                self._render_preview(), language="yaml", read_only=True, id="preview-area"
            )
            yield Button("Write to catalog", id="write-btn", variant="primary")
            yield Button("Cancel", id="cancel-btn")
        yield Footer()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.app.pop_screen()
            return
        if event.button.id != "write-btn":
            return
        self._write()

    def _build_entries(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for c in self.containers:
            entries = self.mappings.get(c.name, [])
            c.volumes = [m.rendered for _, m in entries]
            out[c.name] = c.as_catalog_entry()
        return out

    def _render_preview(self) -> str:
        return yaml_io.to_str({"docker_containers_catalog": self._build_entries()})

    def _write(self) -> None:
        doc = self.state.ensure_docker_catalog()
        existing = doc.get("docker_containers_catalog")
        if not isinstance(existing, dict):
            existing = yaml_io.empty_map()
            doc["docker_containers_catalog"] = existing
        for name, body in self._build_entries().items():
            existing[name] = body
        self.state.save_docker_catalog()
        self.notify(f"Wrote {len(self.containers)} container(s) to catalog", timeout=3)
        # Pop preview, mapping, and import screens.
        self.app.pop_screen()
        self.app.pop_screen()
        self.app.pop_screen()
