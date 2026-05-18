"""Thin wrapper over python-dialog.

Provides the :class:`Dialogs` façade used by flows — ``menu``, ``inputbox``,
``checklist``, ``msgbox``, ``yesno`` — with consistent cancel handling
(``None`` return on Esc/Cancel) and uniform sizing/styling.

If the system ``dialog`` binary is missing at runtime despite
dependency-check having already run, we translate the underlying
``pythondialog`` error into a clean plain-text message telling the
operator to ``apt install dialog`` and exit with status 1 (per E10 /
R12.4).
"""

from __future__ import annotations

import sys
from typing import NoReturn

from dialog import Dialog, ExecutableNotFound


_MISSING_DIALOG_MSG = (
    "error: the 'dialog' system binary is not available at runtime.\n"
    "install it with:  sudo apt install dialog"
)


def _die_missing_dialog() -> NoReturn:
    """Print the clean E10 error to stderr and exit 1.

    Called whenever ``pythondialog`` can't find the ``dialog`` binary —
    either at ``Dialog()`` construction or on the first widget call.
    """
    print(_MISSING_DIALOG_MSG, file=sys.stderr)
    sys.exit(1)


class Dialogs:
    """Façade over :class:`dialog.Dialog` with cancel-as-None semantics.

    The five widget methods used by flows all share the same cancel
    convention: ``Dialog.CANCEL`` and ``Dialog.ESC`` are mapped to
    ``None`` on :meth:`menu`, :meth:`inputbox`, and :meth:`checklist`,
    so callers can write ``if result is None: return`` to mean "back /
    abort". :meth:`yesno` collapses to a plain ``bool``. :meth:`msgbox`
    is fire-and-forget.
    """

    def __init__(self) -> None:
        try:
            self.d = Dialog(dialog="dialog", autowidgetsize=True)
        except (ExecutableNotFound, FileNotFoundError):
            _die_missing_dialog()

    # ---- internal ----------------------------------------------------

    def _call(self, fn, *args, **kwargs):
        """Invoke a ``Dialog`` method, translating E10 at call time too."""
        try:
            return fn(*args, **kwargs)
        except (ExecutableNotFound, FileNotFoundError):
            _die_missing_dialog()

    # ---- widgets -----------------------------------------------------

    def menu(
        self,
        title: str,
        items: list[tuple[str, str]],
    ) -> str | None:
        """Show a single-choice menu.

        ``items`` is a list of ``(tag, display_text)`` pairs. Returns the
        selected tag, or ``None`` if the operator pressed Cancel/Esc.
        """
        code, tag = self._call(
            self.d.menu,
            "",
            choices=list(items),
            title=title,
        )
        if code in (Dialog.CANCEL, Dialog.ESC):
            return None
        return tag

    def inputbox(
        self,
        title: str,
        prompt: str,
        default: str = "",
    ) -> str | None:
        """Show a single-line text prompt.

        Returns the submitted string, or ``None`` on Cancel/Esc. No
        stripping or case folding is applied — the caller owns any
        normalization they want.
        """
        code, text = self._call(
            self.d.inputbox,
            prompt,
            init=default,
            title=title,
        )
        if code in (Dialog.CANCEL, Dialog.ESC):
            return None
        return text

    def checklist(
        self,
        title: str,
        items: list[tuple[str, str, bool]],
    ) -> list[str] | None:
        """Show a multi-select checklist.

        ``items`` is a list of ``(tag, display_text, is_checked)``
        tuples. The boolean is translated to ``dialog``'s ``"on"``/
        ``"off"`` string. Returns the list of checked tags, or ``None``
        on Cancel/Esc.
        """
        choices = [
            (tag, text, "on" if checked else "off")
            for tag, text, checked in items
        ]
        code, tags = self._call(
            self.d.checklist,
            "",
            choices=choices,
            title=title,
        )
        if code in (Dialog.CANCEL, Dialog.ESC):
            return None
        return list(tags)

    def yesno(self, title: str, text: str) -> bool:
        """Show a yes/no confirmation. Returns ``True`` only on ``Yes``."""
        code = self._call(self.d.yesno, text, title=title)
        return code == Dialog.OK

    def msgbox(self, title: str, text: str) -> None:
        """Show an informational message and wait for ``OK``."""
        self._call(self.d.msgbox, text, title=title)
