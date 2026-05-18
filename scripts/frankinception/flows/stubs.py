"""Placeholder flows for deferred menu actions.

Modify-device, remove-device, and run-playbooks are surfaced in the main menu
but not implemented in this iteration. Each stub shows a "not implemented"
message via :class:`~frankinception.ui.dialogs.Dialogs` and returns ``None``
so the caller simply loops back to the main menu. No filesystem writes
occur in this module — that is the whole point of R14.4.
"""

from __future__ import annotations

from ..context import InventoryContext
from ..ui.dialogs import Dialogs


_NOT_IMPLEMENTED = "not yet implemented"


def modify_device(context: InventoryContext, ui: Dialogs) -> None:
    """Stub for the future "modify existing device" flow (R14.1)."""
    ui.msgbox("Modify existing device", _NOT_IMPLEMENTED)


def remove_device(context: InventoryContext, ui: Dialogs) -> None:
    """Stub for the future "remove device" flow (R14.2)."""
    ui.msgbox("Remove device", _NOT_IMPLEMENTED)


def run_playbooks(context: InventoryContext, ui: Dialogs) -> None:
    """Stub for the future "run playbooks" flow (R14.3)."""
    ui.msgbox("Run playbooks", _NOT_IMPLEMENTED)
