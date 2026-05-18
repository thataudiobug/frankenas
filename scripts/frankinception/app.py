"""Main application loop.

Owns the top-level menu shown after dependency checking and context selection,
dispatches to flow modules (`create_device`, stubs), and installs signal
handlers so Ctrl+C / SIGTERM exit cleanly from any dialog screen.
"""

from __future__ import annotations

import signal
import sys
from types import FrameType
from typing import TYPE_CHECKING

from .context import InventoryContext
from .deps import ensure_dependencies

# NOTE: ``Dialogs``, the flow modules, and anything downstream of them import
# ``dialog`` and ``ruamel.yaml`` at their own module top. That's *exactly*
# what ``ensure_dependencies()`` is meant to install when missing, so we
# must not import them at app-module top or a fresh host will hit
# ``ModuleNotFoundError`` before the dep checker can run. Instead we defer
# every such import to inside :func:`run`, which calls
# ``ensure_dependencies()`` first. ``TYPE_CHECKING`` keeps the type hints
# below readable without triggering the runtime import.
if TYPE_CHECKING:  # pragma: no cover - type-only
    from .ui.dialogs import Dialogs


# Main menu tag constants. Kept as module-level sentinels so the dispatch
# table in :func:`_main_menu` and the entries in :data:`_MAIN_MENU_ITEMS`
# agree without string-typing each site.
_MENU_CREATE = "create"
_MENU_MODIFY = "modify"
_MENU_REMOVE = "remove"
_MENU_PLAYBOOKS = "playbooks"
_MENU_QUIT = "quit"

# Main menu rows in the order R3.1 requires: create / modify / remove /
# playbooks / quit. The tag is an internal identifier; the label is what
# the operator sees.
_MAIN_MENU_ITEMS: list[tuple[str, str]] = [
    (_MENU_CREATE, "Create new device"),
    (_MENU_MODIFY, "Modify existing device"),
    (_MENU_REMOVE, "Remove device"),
    (_MENU_PLAYBOOKS, "Run playbooks"),
    (_MENU_QUIT, "Quit"),
]

# Context selection rows. The tag is the enum *value* (``"prod"`` /
# ``"test"``) so we can round-trip straight through ``InventoryContext``.
_CONTEXT_MENU_ITEMS: list[tuple[str, str]] = [
    (InventoryContext.PROD.value, "production inventory"),
    (InventoryContext.TEST.value, "test inventory"),
]


def _sigint_handler(signum: int, frame: FrameType | None) -> None:
    """Exit cleanly on Ctrl+C (R3.4, E6).

    Writes a trailing newline so the shell prompt lands on a fresh line
    after ``dialog`` tears down, then exits 0. No inventory writes
    happen here — any in-progress flow is simply abandoned.
    """
    sys.stdout.write("\n")
    sys.stdout.flush()
    sys.exit(0)


def _install_sigint_handler() -> None:
    """Install the SIGINT handler defined above."""
    signal.signal(signal.SIGINT, _sigint_handler)


def _choose_context(ui: "Dialogs") -> InventoryContext | None:
    """Present the ``prod`` / ``test`` context selector (R2.1).

    Loops so that when the operator picks a context whose ``hosts.yml``
    is missing (R2.4 / R12.1) they get a ``msgbox`` and a fresh shot at
    the selector instead of a crash. Cancel / Esc on the selector
    returns ``None`` so the caller can exit cleanly with status 0
    (R2.3 / R11.4).
    """
    while True:
        tag = ui.menu("Select inventory context", _CONTEXT_MENU_ITEMS)
        if tag is None:
            return None
        context = InventoryContext(tag)
        if not context.hosts_file.exists():
            ui.msgbox(
                "Missing inventory",
                f"{context.hosts_file} does not exist.",
            )
            continue
        return context


def _main_menu(context: InventoryContext, ui: "Dialogs") -> None:
    """Run the main menu loop for an active context (R3.1–R3.5).

    Returns when the operator chooses ``Quit`` or hits Cancel / Esc on
    the menu itself (R3.5 treats Cancel/Esc as Quit). Every other
    choice dispatches to the corresponding flow and then re-displays
    the menu (R3.2).
    """
    # Flow imports are deferred to keep the app module importable before
    # ``ensure_dependencies()`` runs (see note at the top of the file).
    from .flows.create_device import create_device
    from .flows.stubs import modify_device, remove_device, run_playbooks

    title = f"frankinception [{context.value}]"
    while True:
        tag = ui.menu(title, _MAIN_MENU_ITEMS)
        if tag is None or tag == _MENU_QUIT:
            return
        if tag == _MENU_CREATE:
            create_device(context, ui)
        elif tag == _MENU_MODIFY:
            modify_device(context, ui)
        elif tag == _MENU_REMOVE:
            remove_device(context, ui)
        elif tag == _MENU_PLAYBOOKS:
            run_playbooks(context, ui)


def run() -> int:
    """Top-level entry: dep check → context select → main menu.

    Sequence:
      1. :func:`ensure_dependencies` — may prompt / install / exit(1).
      2. Install the SIGINT handler (R3.4 / E6).
      3. Build a :class:`Dialogs` — exits 1 with an E10 message if the
         ``dialog`` binary is missing at runtime (R12.4).
      4. :func:`_choose_context` — Cancel/Esc exits 0 (R2.3 / R11.4);
         missing ``hosts.yml`` loops back (R2.4 / R12.1).
      5. :func:`_main_menu` — loops until Quit / Cancel / Esc (R3.3 /
         R3.5 / R11.3).

    Returns an exit status code, normally 0. Any path that should exit
    non-zero does so directly via :func:`sys.exit` inside the helper
    that detected the error (dep check, missing ``dialog`` binary).
    """
    ensure_dependencies()
    _install_sigint_handler()

    # Imports for everything downstream of the dep check happen here so
    # they only fire after ``ensure_dependencies()`` has had a chance to
    # install what's missing.
    from .ui.dialogs import Dialogs

    ui = Dialogs()

    context = _choose_context(ui)
    if context is None:
        return 0

    _main_menu(context, ui)
    return 0
