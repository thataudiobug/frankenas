"""Import docker-compose / docker run into ``docker_containers_catalog``.

Flow:
1. User pastes text or types a file path.
2. We parse, then walk every volume against ``docker_bind_catalog``.
3. For each volume we offer two choices:
   * leave it verbatim, or
   * edit it manually (with ``Ctrl+B`` to splice in a bind variable).
   Auto-matched volumes that the bind matcher already rewrote are still
   editable from the same screen.
4. Show the resulting catalog YAML diff and confirm the write.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static, TextArea

from .. import yaml_io
from ..bind_mapper import VolumeMatch, match_volume
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
        self._compose_dir: Path | None = None
        """Directory of the most recently loaded file, used to find ``.env``
        files at variable-resolution time. ``None`` for pasted text."""

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
        self._compose_dir = path.parent
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

        # Detect ``${VAR}`` references. If there are none, skip straight
        # to volume mapping; otherwise let the user resolve them first.
        from .. import compose_vars
        from .var_resolver import VariableResolverScreen

        variables = compose_vars.find_variables(containers)
        if not variables:
            self.app.push_screen(_VolumeMappingScreen(self.state, containers))
            return

        env_vars = compose_vars.collect_env_vars(self._compose_dir, containers)
        # Pre-populate any variable whose name exactly matches an env var.
        # The user can still override these in the resolver UI.
        for var in variables:
            if var.name in env_vars and not var.has_resolution:
                var.resolved = env_vars[var.name]

        def _on_resolved(resolved: list | None) -> None:
            if resolved is None:
                return
            compose_vars.apply_resolutions(containers, resolved)
            self.app.push_screen(_VolumeMappingScreen(self.state, containers))

        self.app.push_screen(
            VariableResolverScreen(self.state, variables, env_vars),
            _on_resolved,
        )


class _VolumeMappingScreen(Screen):
    """Walk through every volume; user can re-map any of them."""

    BINDINGS = [
        Binding("escape", "back", "Back"),
    ]

    def __init__(self, state: AppState, containers: list[ParsedContainer]) -> None:
        super().__init__()
        self.state = state
        self.containers = containers
        # Flat row index → (container_name, volume_index) for table lookups.
        self._row_index: list[tuple[str, int]] = []
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
                "Each volume is listed below — Enter on a row to change its mapping. "
                "Use this to re-map auto-detected entries that picked the wrong bind.",
                id="map-help",
            )
            with Vertical(classes="pane"):
                yield DataTable(
                    id="map-table", cursor_type="row", zebra_stripes=True
                )
            yield Button("Continue to preview", id="continue-btn", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#map-table", DataTable)
        table.add_columns("", "Container", "Volume", "Current mapping")
        self._refill(table)
        table.focus()

    def action_back(self) -> None:
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "continue-btn":
            self._continue_to_preview()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        # row_key.value is the flat row index as a string.
        if event.row_key.value is None:
            return
        idx = int(str(event.row_key.value))
        if idx >= len(self._row_index):
            return
        cname, vidx = self._row_index[idx]
        raw, current = self.mappings[cname][vidx]
        self.app.push_screen(
            _BindChoiceScreen(self.state, cname, vidx, raw, current),
            self._on_choice,
        )

    def _continue_to_preview(self) -> None:
        # Block on any volume the user hasn't resolved yet.
        unresolved = [
            (cname, vidx, raw)
            for cname, entries in self.mappings.items()
            for vidx, (raw, m) in enumerate(entries)
            if m.needs_user_choice
        ]
        if unresolved:
            self.notify(
                f"{len(unresolved)} volume(s) still need mapping. Enter on each red row.",
                severity="warning",
                timeout=4,
            )
            return
        self.app.push_screen(_PreviewScreen(self.state, self.containers, self.mappings))

    def _on_choice(self, result: tuple[str, int, VolumeMatch] | None) -> None:
        if result is None:
            return
        cname, vidx, new_match = result
        raw, _ = self.mappings[cname][vidx]
        self.mappings[cname][vidx] = (raw, new_match)
        self._refill(self.query_one("#map-table", DataTable))

    def _refill(self, table: DataTable) -> None:
        table.clear()
        self._row_index = []
        idx = 0
        for c in self.containers:
            entries = self.mappings.get(c.name, [])
            if not entries:
                # Show a placeholder so containers with no volumes are visible.
                continue
            for vidx, (raw, m) in enumerate(entries):
                self._row_index.append((c.name, vidx))
                if m.needs_user_choice:
                    status = "[red]?[/red]"
                    rendered = "[red]unmapped — Enter to fix[/red]"
                elif m.bind_key:
                    status = "[green]✓[/green]"
                    rendered = m.rendered
                else:
                    status = "[yellow]·[/yellow]"
                    rendered = f"{m.rendered}  [dim](verbatim)[/dim]"
                table.add_row(status, c.name, raw, rendered, key=str(idx))
                idx += 1


class _BindChoiceScreen(ModalScreen[tuple[str, int, VolumeMatch] | None]):
    """Pick what to do with a single volume.

    Reachable both for unmatched volumes (during initial parse) and for
    re-mapping volumes the auto-matcher rewrote against the wrong bind.
    Two choices: edit manually (with ``Ctrl+B`` for bind insertion) or
    keep the volume verbatim.
    """

    BINDINGS = [
        # Keyboard shortcuts mirror the buttons so the screen is fully usable
        # without a mouse.
        Binding("m", "edit_manual", "Edit manually"),
        Binding("k", "keep_verbatim", "Keep verbatim"),
        Binding("up", "focus_previous", "Prev", show=False),
        Binding("down", "focus_next", "Next", show=False),
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
        if self.match.needs_user_choice:
            heading = "[b]Unmapped volume[/b]"
            current = "(no bind selected)"
        elif self.match.bind_key:
            heading = "[b]Re-map volume[/b]"
            current = (
                f"auto-matched against [b]{self.match.bind_key}[/b] → {self.match.rendered}"
            )
        else:
            heading = "[b]Re-map volume[/b]"
            current = "kept verbatim (no bind rewrite)"

        yield Header(show_clock=False)
        with Vertical(id="bind-choice"):
            yield Static(
                f"{heading}\n"
                f"container: {self.container}\n"
                f"raw:       {self.raw_volume}\n"
                f"host path: {self.match.raw_host_path}\n"
                f"in-ctnr:   {self.match.raw_container_path}\n"
                f"status:    {current}",
                id="bind-info",
            )
            yield Label("Pick an action (click or press the highlighted key):")
            yield Button("Edit [u]m[/u]anually…", id="manual-btn", variant="primary")
            yield Button("[u]K[/u]eep verbatim (no rewrite)", id="keep-btn")
            yield Button("Cancel (Esc)", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "manual-btn":
            self._edit_manually()
        elif event.button.id == "keep-btn":
            self._keep_verbatim()

    # ---- key actions delegate to the same handlers --------------------

    def action_edit_manual(self) -> None:
        self._edit_manually()

    def action_keep_verbatim(self) -> None:
        self._keep_verbatim()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _keep_verbatim(self) -> None:
        verbatim = VolumeMatch(
            rendered=self.raw_volume,
            bind_key=None,
            raw_host_path=self.match.raw_host_path,
            raw_container_path=self.match.raw_container_path,
            mode=self.match.mode,
            needs_user_choice=False,
        )
        self.dismiss((self.container, self.idx, verbatim))

    def _edit_manually(self) -> None:
        self.app.push_screen(
            _ManualVolumeScreen(self.state, self.match), self._on_manual_done
        )

    def _on_manual_done(self, rendered: str | None) -> None:
        if rendered is None:
            return
        host, container, mode = _split_safe(rendered)
        new_match = VolumeMatch(
            rendered=rendered,
            bind_key=None,
            raw_host_path=host or self.match.raw_host_path,
            raw_container_path=container or self.match.raw_container_path,
            mode=mode,
            needs_user_choice=False,
        )
        self.dismiss((self.container, self.idx, new_match))


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


class _ManualVolumeScreen(ModalScreen[str | None]):
    """Free-form editor for a single volume string.

    Used in two situations:

    * The user pressed ``m`` / clicked Edit Manually on the bind-choice screen
      to fully customise the volume.
    * They picked an existing bind whose ``src`` doesn't actually contain the
      original host path; we land here pre-seeded with
      ``{{ docker_bind_catalog.<key>.mnt }}/:/<container>`` so they can fill
      in the subpath themselves.

    Includes an "Insert bind variable" helper because the project's
    convention is ``{{ docker_bind_catalog.<key>.mnt }}/<subpath>:/<inside>``
    and typing those expressions by hand is tedious and error-prone.
    """

    BINDINGS = [
        Binding("ctrl+s", "save", "Save"),
        Binding("ctrl+b", "insert_bind", "Insert bind"),
        Binding("up", "focus_previous", "Prev", show=False),
        Binding("down", "focus_next", "Next", show=False),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        state: AppState,
        match: VolumeMatch,
        seeded: str | None = None,
    ) -> None:
        super().__init__()
        self.state = state
        # If the caller pre-seeded a starting string (e.g. from "use existing
        # bind" when the splice failed) honour it; otherwise reuse the current
        # rendered value so the user can tweak rather than retype.
        if seeded is not None:
            self._initial = seeded
        else:
            self._initial = match.rendered or (
                f"{match.raw_host_path}:{match.raw_container_path}"
                + (f":{match.mode}" if match.mode else "")
            )

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="vol-edit"):
            yield Static(
                "[b]Edit volume manually[/b]\n"
                "Free-form volume string. Catalog convention is\n"
                "[dim]'{{ docker_bind_catalog.<key>.mnt }}/<subpath>:/<container>[:mode]'[/dim]",
            )
            yield Label("Volume:")
            yield Input(value=self._initial, id="vol-input")
            with Horizontal(classes="vol-row"):
                yield Button("Insert bind var (Ctrl+B)", id="insert-btn")
                yield Button("Save (Ctrl+S)", id="save-btn", variant="primary")
                yield Button("Cancel (Esc)", id="cancel-btn")
        yield Footer()

    def on_mount(self) -> None:
        # Land focus in the input so the user can start typing immediately.
        # If the seed has a trailing slash before ``:``, drop the cursor at
        # that point — that's the natural insertion site.
        inp: Input = self.query_one("#vol-input", Input)
        inp.focus()
        target = self._suggest_cursor(self._initial)
        if target is not None:
            inp.cursor_position = target

    @staticmethod
    def _suggest_cursor(value: str) -> int | None:
        """Where the cursor should sit when the screen first opens.

        For seeded strings like ``{{ ... }}/:/data``, the user needs to type
        the subpath right after the closing ``}``. We look for ``mnt }}/``
        followed by ``:`` and place the cursor between them.
        """
        marker = "mnt }}/"
        i = value.find(marker)
        if i == -1:
            return None
        cursor = i + len(marker)
        # If the very next char is ``:`` we drop the cursor right before it.
        return cursor

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-btn":
            self.dismiss(None)
        elif event.button.id == "save-btn":
            self.action_save()
        elif event.button.id == "insert-btn":
            self.action_insert_bind()

    def action_save(self) -> None:
        value = self.query_one("#vol-input", Input).value.strip()
        if not value:
            self.notify("Volume can't be empty", severity="error")
            return
        self.dismiss(value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_insert_bind(self) -> None:
        from .catalog_picker import SinglePickerScreen

        bind_cat = self.state.bind_catalog()
        if not bind_cat:
            self.notify("No binds defined yet", severity="warning")
            return

        def _on_pick(key: str | None) -> None:
            if key is None:
                return
            self._splice_bind_at_cursor(key)

        self.app.push_screen(
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

    def _splice_bind_at_cursor(self, key: str) -> None:
        """Insert the chosen bind expression at the current cursor position."""
        inp: Input = self.query_one("#vol-input", Input)
        snippet = "{{ docker_bind_catalog." + key + ".mnt }}"
        text = inp.value
        pos = inp.cursor_position
        new_text = text[:pos] + snippet + text[pos:]
        inp.value = new_text
        inp.cursor_position = pos + len(snippet)
        inp.focus()


def _split_safe(volume: str) -> tuple[str | None, str | None, str | None]:
    """Lenient splitter for a free-form volume — never raises."""
    parts = volume.split(":")
    if len(parts) == 2:
        return parts[0], parts[1], None
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return None, None, None
