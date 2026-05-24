"""Reusable picker screen for selecting catalog entries.

Modes:

* ``"single"`` — radio-list, one selection or none.
* ``"multi"``  — checklist, any number of selections.

Returns the selected key(s) via ``self.dismiss``.
"""

from __future__ import annotations

from typing import Any, Iterable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Label, OptionList, SelectionList, Static
from textual.widgets.option_list import Option


class SinglePickerScreen(ModalScreen[str | None]):
    """One-of-many selection. Returns the selected key, or None."""

    BINDINGS = [
        Binding("enter", "confirm", "Select"),
        Binding("c", "clear", "Clear selection"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        title: str,
        entries: dict[str, Any],
        current: str | None,
        describe: callable | None = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._entries = entries
        self._current = current
        self._describe = describe or (lambda _k, v: _summarise(v))

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="picker"):
            yield Static(f"[b]{self._title}[/b]", id="picker-title")
            yield Static(
                "Enter to choose, c to clear, Esc to cancel",
                id="picker-help",
            )
            options = [
                Option(self._render(key, value), id=key)
                for key, value in self._entries.items()
            ]
            yield OptionList(*options, id="picker-list")
        yield Footer()

    def on_mount(self) -> None:
        ol: OptionList = self.query_one("#picker-list", OptionList)
        if self._current and self._current in self._entries:
            keys = list(self._entries.keys())
            ol.highlighted = keys.index(self._current)
        ol.focus()

    def _render(self, key: str, value: Any) -> str:
        marker = "●" if key == self._current else "○"
        summary = self._describe(key, value)
        if summary:
            return f"{marker} {key}    [dim]{summary}[/dim]"
        return f"{marker} {key}"

    def action_confirm(self) -> None:
        ol: OptionList = self.query_one("#picker-list", OptionList)
        if ol.highlighted is None:
            return
        option = ol.get_option_at_index(ol.highlighted)
        self.dismiss(option.id)

    def action_clear(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        # Distinct from action_clear: cancel shouldn't be ambiguous with
        # "no selection", so we use a sentinel by dismissing None and let
        # the caller treat None as "leave unchanged" if they passed a sentinel
        # default. For the host editor we treat both as "no change", which is
        # safe because we only write changes that diverge from current state.
        self.dismiss(None)


class MultiPickerScreen(ModalScreen[list[str] | None]):
    """Multi-selection. Returns the list of selected keys, or None on cancel."""

    BINDINGS = [
        Binding("space", "toggle", "Toggle"),
        Binding("enter", "confirm", "Save"),
        Binding("a", "select_all", "All"),
        Binding("n", "select_none", "None"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(
        self,
        title: str,
        entries: dict[str, Any],
        current: Iterable[str],
        describe: callable | None = None,
    ) -> None:
        super().__init__()
        self._title = title
        self._entries = entries
        self._current = set(current)
        self._describe = describe or (lambda _k, v: _summarise(v))

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="picker"):
            yield Static(f"[b]{self._title}[/b]", id="picker-title")
            yield Static(
                "Space to toggle, a/n select all/none, Enter to save, Esc to cancel",
                id="picker-help",
            )
            selections = [
                (self._label(key, value), key, key in self._current)
                for key, value in self._entries.items()
            ]
            yield SelectionList[str](*selections, id="picker-list")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#picker-list", SelectionList).focus()

    def _label(self, key: str, value: Any) -> str:
        summary = self._describe(key, value)
        if summary:
            return f"{key}    [dim]{summary}[/dim]"
        return key

    def action_toggle(self) -> None:
        # SelectionList handles space natively, but rebinding ensures it works
        # consistently when this screen has focus.
        sl: SelectionList = self.query_one("#picker-list", SelectionList)
        if sl.highlighted is not None:
            sl.toggle(sl.get_option_at_index(sl.highlighted).id)

    def action_select_all(self) -> None:
        self.query_one("#picker-list", SelectionList).select_all()

    def action_select_none(self) -> None:
        self.query_one("#picker-list", SelectionList).deselect_all()

    def action_confirm(self) -> None:
        sl: SelectionList = self.query_one("#picker-list", SelectionList)
        self.dismiss(list(sl.selected))

    def action_cancel(self) -> None:
        self.dismiss(None)


def _summarise(value: Any) -> str:
    """One-line summary of a catalog entry for inline display."""
    if isinstance(value, dict):
        if "image" in value:
            return str(value["image"])
        bits = []
        for k in ("cores", "memory_mb", "disk_size_gb", "disk_type", "bridge", "network"):
            if k in value:
                bits.append(f"{k}={value[k]}")
        if bits:
            return ", ".join(bits)
        return ", ".join(f"{k}={v}" for k, v in list(value.items())[:3])
    if value is None:
        return ""
    return str(value)
