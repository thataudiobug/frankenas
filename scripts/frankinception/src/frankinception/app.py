"""Textual application entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from textual.app import App

from . import paths
from .screens.host_list import HostListScreen
from .state import AppState


CSS = """
Screen {
    layout: vertical;
}

#banner, #host-heading, #overrides-heading, #map-help, #import-help,
#play-heading, #run-heading, #preview-heading, #picker-title, #picker-help {
    padding: 1 2;
    background: $boost;
}

#body, #host-body, #import-row, #play-options {
    height: auto;
}

#hosts, #catalog-table, #plays, #containers {
    height: 1fr;
    border: round $primary;
    margin: 1 1 1 1;
}

#actions, #groups-pane, #catalog-pane {
    width: 1fr;
    padding: 1 2;
}

#picker {
    width: 80%;
    height: 80%;
    padding: 1 2;
    border: thick $primary;
    background: $surface;
}

#bind-choice, #new-bind, #ctnr-edit, #picker {
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

.dim {
    color: $text-muted;
}

#hint {
    margin-top: 1;
    color: $text-muted;
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
