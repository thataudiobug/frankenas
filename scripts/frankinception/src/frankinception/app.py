"""Textual application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from textual.app import App

from . import paths
from .screens.host_list import HostListScreen
from .state import AppState
from . import __version__


CSS = """
Screen {
    layout: vertical;
}

#banner, #host-heading, #overrides-heading, #map-help, #import-help,
#play-heading, #run-heading, #preview-heading, #picker-title, #picker-help {
    padding: 1 2;
    background: $boost;
}

#body, #host-body, #import-row, #play-options, #play-extra {
    height: auto;
}

/* The extra-flags row needs the input to take most of the width and the
   label to sit comfortably against it. */
#play-extra Label {
    padding: 1 1 0 1;
}

#play-extra Input {
    width: 1fr;
}

/* A pane is a bordered, padded container that sits next to other panes.
   Anchoring the border at this level (not on the inner DataTable) keeps
   adjacent panel edges aligned and stable on cursor moves. */
.pane {
    width: 1fr;
    border: round $primary;
    padding: 0 1;
    margin: 1 1;
}

#body {
    height: 1fr;
}

#host-body {
    height: 1fr;
}

#hosts, #catalog-table, #plays, #containers, #map-table {
    height: 1fr;
    border: none;
    margin: 0;
}

#actions, #groups-pane, #catalog-pane {
    width: 1fr;
}

#picker {
    width: 80%;
    height: 80%;
    padding: 1 2;
    border: thick $primary;
    background: $surface;
}

#bind-choice, #new-bind, #ctnr-edit, #picker, #vol-edit {
    margin: 2 4;
    padding: 1 2;
    border: thick $accent;
    background: $surface;
}

#preview-area, #paste-area {
    height: 1fr;
    border: round $primary;
    margin: 1 1;
}

#run-log {
    height: 1fr;
    border: round $primary;
    margin: 1 1;
}

/* The play-runner output screen is a ModalScreen — modal screens size
   to their content by default. We want the run log to dominate the
   layout, so pin the outer container to fill the screen and constrain
   the input/button rows to their natural height. */
.run-output-screen {
    align: left top;
}

.run-output-screen > Vertical {
    width: 100%;
    height: 100%;
}

#run-input-row, #run-bottom-row {
    height: auto;
    padding: 0 1;
}

#run-input-row Input {
    width: 1fr;
}

.dim {
    color: $text-muted;
}

#hint {
    margin-top: 1;
    color: $text-muted;
}

Button {
    margin: 1 0;
}
"""


class FrankinceptionApp(App):
    """Top-level Textual app."""

    CSS = CSS
    TITLE = "frankinception"

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state

    def on_mount(self) -> None:
        self.push_screen(HostListScreen(self.state))


@click.command()
@click.version_option(version=__version__, prog_name="frankinception")
@click.option(
    "--inventory",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Path to inventory directory. Defaults to ansible.cfg's setting.",
)
def main(inventory: Path | None) -> None:
    """TUI for managing the frankenas Ansible inventory."""
    layout = paths.discover(inventory)
    if not layout.hosts_file.is_file():
        click.echo(
            f"hosts.yml not found at {layout.hosts_file}. "
            "Run from the project root or pass --inventory.",
            err=True,
        )
        sys.exit(1)
    state = AppState.load(layout)
    FrankinceptionApp(state).run()


if __name__ == "__main__":
    main()
