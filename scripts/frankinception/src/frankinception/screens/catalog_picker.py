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
        # ``s`` mirrors the multi-picker so muscle memory carries over. Enter
        # also works via on_option_list_option_selected — see the handler.
        Binding("s", "confirm", "Save"),
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
                "Enter or s to select · c to clear · Esc to cancel",
                id="picker-help",
            )
            options = [
                Option(self._render_option(key, value), id=key)
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

    def _render_option(self, key: str, value: Any) -> str:
        marker = "●" if key == self._current else "○"
        summary = self._describe(key, value)
        if summary:
            return f"{marker} {key}    [dim]{summary}[/dim]"
        return f"{marker} {key}"

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        # OptionList fires this on Enter; using its native event is more
        # reliable than a screen-level "enter" binding (which the focused
        # widget would consume first).
        if event.option.id is not None:
            self.dismiss(event.option.id)

    def action_confirm(self) -> None:
        ol: OptionList = self.query_one("#picker-list", OptionList)
        if ol.highlighted is None:
            return
        option = ol.get_option_at_index(ol.highlighted)
        self.dismiss(option.id)

    def action_clear(self) -> None:
        self.dismiss(None)

    def action_cancel(self) -> None:
        ol: OptionList = self.query_one("#picker-list", OptionList)
        # If the user moved the highlight away from the current selection but
        # then hits Esc, treat that as "I changed my mind". No data loss to
        # warn about — the picker hasn't written anything yet.
        highlighted_id: str | None = None
        if ol.highlighted is not None:
            highlighted_id = ol.get_option_at_index(ol.highlighted).id
        if highlighted_id is not None and highlighted_id != self._current:
            def _on_confirm(discard: bool | None) -> None:
                if discard:
                    self.dismiss(None)

            self.app.push_screen(_DiscardConfirm(), _on_confirm)
            return
        self.dismiss(None)


class MultiPickerScreen(ModalScreen[list[str] | None]):
    """Multi-selection. Returns the list of selected keys, or None on cancel."""

    BINDINGS = [
        Binding("space", "toggle", "Toggle"),
        Binding("s", "confirm", "Save"),
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
                "Space to toggle, a/n select all/none, [b]s to save[/b], Esc to cancel",
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
        sl: SelectionList = self.query_one("#picker-list", SelectionList)
        if set(sl.selected) == self._current:
            # Nothing changed — quiet exit.
            self.dismiss(None)
            return
        # Unsaved edits — prompt before throwing them away.
        def _on_confirm(discard: bool | None) -> None:
            if discard:
                self.dismiss(None)

        self.app.push_screen(_DiscardConfirm(), _on_confirm)


class _DiscardConfirm(ModalScreen[bool]):
    """Tiny yes/no dialog for ``Esc`` with unsaved edits."""

    BINDINGS = [
        Binding("y", "yes", "Discard", show=True),
        Binding("n", "no", "Keep editing", show=True),
        Binding("up", "focus_previous", "Prev", show=False),
        Binding("down", "focus_next", "Next", show=False),
        Binding("escape", "no", "Keep editing", show=False),
    ]

    def compose(self) -> ComposeResult:
        from textual.widgets import Button as _Button

        with Vertical(id="picker"):
            yield Static(
                "[b]Unsaved changes[/b]\n\n"
                "Discard your selections and close the picker?",
                id="picker-title",
            )
            yield Static(
                "y to discard · n or Esc to keep editing", id="picker-help"
            )
            yield _Button("Discard (y)", id="discard-btn", variant="error")
            yield _Button("Keep editing (n)", id="keep-btn", variant="primary")

    def on_button_pressed(self, event) -> None:
        from textual.widgets import Button as _Button

        if event.button.id == "discard-btn":
            self.dismiss(True)
        elif event.button.id == "keep-btn":
            self.dismiss(False)

    def action_yes(self) -> None:
        self.dismiss(True)

    def action_no(self) -> None:
        self.dismiss(False)


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
