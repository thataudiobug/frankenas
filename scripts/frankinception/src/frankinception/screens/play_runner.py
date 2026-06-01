"""Pick a playbook, choose a limit scope, and run it.

Flow:

1. ``PlayRunnerScreen`` — list of playbooks; Enter on a row chooses it.
2. ``_LimitTypeScreen`` — three options: no limit, limit by host, limit by
   group. Returned value drives step 3.
3. ``SinglePickerScreen`` (re-used) — only shown for the host/group choices,
   pre-populated from the inventory.
4. ``_RunOutputScreen`` — streams ansible-playbook output live.

Every play runs under a pseudo-terminal so interactive prompts (sudo,
``vars_prompt``, ``pause``, SSH host-key acceptance) all just work without
the user having to know in advance whether a given play will prompt. The
output screen always exposes Send / Enter / Ctrl+C controls; non-interactive
plays simply ignore them.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import struct
import tempfile
import termios
import time
from pathlib import Path

from rich.text import Text

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Static,
)

from .. import runner
from ..plays import Playbook, list_playbooks
from ..state import AppState
from .catalog_picker import SinglePickerScreen


class PlayRunnerScreen(Screen):
    BINDINGS = [
        Binding("escape", "back", "Back"),
        Binding("r", "refresh", "Refresh"),
    ]

    def __init__(self, state: AppState, default_limit: str | None = None) -> None:
        super().__init__()
        self.state = state
        # ``default_limit`` is accepted for backwards-compat with callers but
        # ignored — limit selection is now an explicit step after picking a
        # playbook so the user isn't surprised by which host they're targeting.
        del default_limit

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(
                f"[b]Run playbook[/b]   plays dir: [cyan]{self.state.layout.plays_dir}[/cyan]\n"
                "Enter on a row to choose a limit scope, then run.",
                id="play-heading",
            )
            yield DataTable(id="plays", cursor_type="row", zebra_stripes=True)
            with Horizontal(id="play-options"):
                yield Checkbox("Check mode (--check)", id="check-cb")
                yield Checkbox(
                    "Use vault (prompts for password if needed)",
                    id="vault-cb",
                    value=True,
                )
            with Horizontal(id="play-extra"):
                yield Label("Extra ansible-playbook flags:")
                yield Input(
                    placeholder="e.g.  -vvv --tags docker --skip-tags slow",
                    id="extra-input",
                )
        yield Footer()

    def on_mount(self) -> None:
        table: DataTable = self.query_one("#plays", DataTable)
        table.add_columns("Playbook", "Description")
        self._refill(table)
        table.focus()

    def action_back(self) -> None:
        self.app.pop_screen()

    def action_refresh(self) -> None:
        self._refill(self.query_one("#plays", DataTable))

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.row_key.value is not None:
            self._choose_limit_then_run(str(event.row_key.value))

    # ---- limit-scope flow -------------------------------------------

    def _choose_limit_then_run(self, name: str) -> None:
        play = next((p for p in self._plays if p.name == name), None)
        if play is None:
            return

        def _on_scope(choice: str | None) -> None:
            # choice is "none" | "host" | "group" | None (cancel)
            if choice is None:
                return
            if choice == "none":
                self._run(play, limit=None)
                return
            if choice == "host":
                self._pick_host_then_run(play)
            elif choice == "group":
                self._pick_group_then_run(play)

        self.app.push_screen(_LimitTypeScreen(play.name), _on_scope)

    def _pick_host_then_run(self, play: Playbook) -> None:
        hosts = self.state.inventory.hosts()
        if not hosts:
            self.notify("No hosts in inventory", severity="warning")
            return
        # Build a "host -> groups" describe so the user has context. The
        # picker accepts a dict, so we use None values purely for keys.
        entries = {h: None for h in hosts}
        inv = self.state.inventory

        def describe(key: str, _value: object) -> str:
            direct = inv.direct_groups_of(key)
            return ", ".join(direct) if direct else ""

        def _on_pick(host: str | None) -> None:
            if host:
                self._run(play, limit=host)

        self.app.push_screen(
            SinglePickerScreen(
                f"Limit '{play.name}' to host", entries, None, describe=describe
            ),
            _on_pick,
        )

    def _pick_group_then_run(self, play: Playbook) -> None:
        groups = sorted(self.state.inventory.groups())
        if not groups:
            self.notify("No groups in inventory", severity="warning")
            return
        entries = {g: None for g in groups}
        inv = self.state.inventory

        def describe(key: str, _value: object) -> str:
            # Count how many hosts the group covers including children.
            count = sum(1 for h in inv.hosts() if key in inv.all_groups_of(h))
            return f"{count} host{'s' if count != 1 else ''}"

        def _on_pick(group: str | None) -> None:
            if group:
                self._run(play, limit=group)

        self.app.push_screen(
            SinglePickerScreen(
                f"Limit '{play.name}' to group", entries, None, describe=describe
            ),
            _on_pick,
        )

    def _run(self, play: Playbook, limit: str | None) -> None:
        check = self.query_one("#check-cb", Checkbox).value
        use_vault = self.query_one("#vault-cb", Checkbox).value
        extra_args = self._parse_extra_args()
        if extra_args is None:
            return  # parser already notified the user
        if use_vault:
            self._run_with_vault(play, limit, check, extra_args)
        else:
            self._launch(play, limit, check, vault_password=None, extra_args=extra_args)

    def _parse_extra_args(self) -> list[str] | None:
        """Tokenise the extra-args input the way a shell would.

        Returns ``[]`` for empty input, ``None`` when the input fails to
        parse (we surface the error to the user and skip the run rather
        than silently dropping flags).
        """
        import shlex

        raw = self.query_one("#extra-input", Input).value.strip()
        if not raw:
            return []
        try:
            return shlex.split(raw)
        except ValueError as exc:
            self.notify(
                f"Couldn't parse extra flags ({exc}). Check your quoting.",
                severity="error",
                timeout=6,
            )
            return None

    def _run_with_vault(
        self,
        play: Playbook,
        limit: str | None,
        check: bool,
        extra_args: list[str],
    ) -> None:
        """If we already have a vault password from this session, reuse it.
        Otherwise prompt and remember it for the rest of the session.
        """
        cfg = self.state.vault_config
        if cfg is not None and cfg.password:
            self._launch(
                play, limit, check, vault_password=cfg.password, extra_args=extra_args
            )
            return
        # Prompt — same screen the secrets workflow uses.
        from .secrets import _VaultPasswordPrompt

        def _on_pw(result: object) -> None:
            if result is None:
                return
            self.state.vault_config = result  # type: ignore[assignment]
            pw = self.state.vault_config.password  # type: ignore[union-attr]
            if not pw:
                self.notify("No password provided", severity="error")
                return
            self._launch(
                play, limit, check, vault_password=pw, extra_args=extra_args
            )

        self.app.push_screen(
            _VaultPasswordPrompt(self.state, vault_path=self.state.vault_path),
            _on_pw,
        )

    def _launch(
        self,
        play: Playbook,
        limit: str | None,
        check: bool,
        vault_password: str | None,
        extra_args: list[str] | None = None,
    ) -> None:
        invocation = runner.build(
            playbook=play.path,
            project_root=self.state.layout.project_root,
            inventory_dir=self.state.layout.inventory_dir,
            limit=limit,
            check=check,
            vault_password=vault_password,
            extra_args=extra_args,
        )
        self.app.push_screen(_RunOutputScreen(invocation, play))

    # ---- helpers -----------------------------------------------------

    def _refill(self, table: DataTable) -> None:
        table.clear()
        self._plays: list[Playbook] = list_playbooks(self.state.layout.plays_dir)
        for p in self._plays:
            table.add_row(p.name, p.description, key=p.name)


class _LimitTypeScreen(ModalScreen[str | None]):
    """Three-way modal: no limit, by host, or by group.

    Returns one of ``"none"`` / ``"host"`` / ``"group"`` (or None on cancel).
    """

    BINDINGS = [
        Binding("n", "no_limit", "No limit"),
        Binding("h", "by_host", "Host"),
        Binding("g", "by_group", "Group"),
        Binding("up", "focus_previous", "Prev", show=False),
        Binding("down", "focus_next", "Next", show=False),
        Binding("escape", "cancel", "Cancel"),
    ]

    def __init__(self, play_name: str) -> None:
        super().__init__()
        self._play_name = play_name

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="bind-choice"):
            yield Static(
                f"[b]Limit scope for[/b] {self._play_name}\n"
                "How should this run be scoped?",
                id="bind-info",
            )
            yield Label("Pick an option (click or press the highlighted key):")
            yield Button(
                "[u]N[/u]o limit (run on all hosts the play targets)",
                id="none-btn",
                variant="primary",
            )
            yield Button("Limit by [u]h[/u]ost…", id="host-btn")
            yield Button("Limit by [u]g[/u]roup…", id="group-btn")
            yield Button("Cancel (Esc)", id="cancel-btn")
        yield Footer()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "none-btn":
            self.dismiss("none")
        elif event.button.id == "host-btn":
            self.dismiss("host")
        elif event.button.id == "group-btn":
            self.dismiss("group")
        elif event.button.id == "cancel-btn":
            self.dismiss(None)

    def action_no_limit(self) -> None:
        self.dismiss("none")

    def action_by_host(self) -> None:
        self.dismiss("host")

    def action_by_group(self) -> None:
        self.dismiss("group")

    def action_cancel(self) -> None:
        self.dismiss(None)


class _RunOutputScreen(ModalScreen[None]):
    """Stream subprocess output live under a pseudo-terminal.

    A PTY is the only run mode. ``ansible-playbook`` thinks it's on a
    real terminal, so anything that prompts (sudo/become passwords,
    ``vars_prompt``, ``pause``, SSH host-key acceptance) just works.
    Plays that don't prompt run identically — the input controls below
    the log are simply unused.

    Stability notes:

    * **Detached-widget guard.** If the user pops the screen mid-run, we
      stop writing to the (now-disposed) RichLog. We still wait for the
      subprocess to exit so the transient password file can be unlinked,
      but writes are silently dropped. ``Worker`` is exclusive so the
      reader task gets cancelled when we leave.
    * **Plain output by default.** ``ANSIBLE_FORCE_COLOR=0`` and
      ``NO_COLOR=1`` keep escape codes out of the log. Anything the user
      explicitly sets in their parent env wins (we use ``setdefault``).
    """

    BINDINGS = [
        Binding("escape", "back", "Close"),
    ]

    def __init__(
        self,
        invocation: runner.Invocation,
        play: Playbook,
    ) -> None:
        # ``classes`` lets app-level CSS target this screen without
        # depending on the underscore-prefixed Python class name.
        super().__init__(classes="run-output-screen")
        self.invocation = invocation
        self.play = play
        self._proc: asyncio.subprocess.Process | None = None
        self._cleaned_up = False
        self._unmounted = False
        self._master_fd: int | None = None
        self._reader_attached = False
        self._read_buffer = b""
        # Plain-text mirror of the run output. We keep this so the user
        # can save it to a file from within the TUI (color codes stripped)
        # — selecting text directly is finicky over SSH because Textual
        # captures the mouse for its own use.
        self._plain_output: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical():
            yield Static(self._heading_text(), id="run-heading")
            yield RichLog(id="run-log", highlight=False, markup=False, wrap=False)
            with Horizontal(id="run-input-row"):
                yield Input(
                    placeholder="type input then click Send (or Enter button for blank line)",
                    id="run-input",
                )
                yield Button("Send", id="send-btn", variant="primary")
                yield Button("↵ Enter", id="enter-btn")
                yield Button("^C", id="ctrlc-btn", variant="warning")
            with Horizontal(id="run-bottom-row"):
                yield Button("Save log…", id="save-log-btn")
                yield Button("Close", id="close-btn")
        yield Footer()

    def on_mount(self) -> None:
        # ``exclusive=True`` so the worker is auto-cancelled if a second
        # one would somehow start.
        self.run_worker(self._run_subprocess(), exclusive=True, name="ansible")

    async def on_unmount(self) -> None:
        # Mark the widget detached so the reader stops writing to it,
        # then let the subprocess finish on its own (or get terminated
        # by ``action_back``).
        self._unmounted = True
        self._detach_pty_reader()

    def action_back(self) -> None:
        if self._proc and self._proc.returncode is None:
            self.notify("Process still running — terminating", severity="warning")
            self._terminate_proc()
        self._cleanup_sensitive()
        self.app.pop_screen()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "close-btn":
            self.action_back()
        elif bid == "send-btn":
            self._send_input(newline=True)
        elif bid == "enter-btn":
            self._write_to_pty(b"\n")
        elif bid == "ctrlc-btn":
            # 0x03 is the standard SIGINT signal byte. PTYs deliver it
            # as a real Ctrl+C to the foreground process group.
            self._write_to_pty(b"\x03")
        elif bid == "save-log-btn":
            self._save_log()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "run-input":
            self._send_input(newline=True)

    # ---- subprocess plumbing ----------------------------------------

    async def _run_subprocess(self) -> None:
        self._safe_write(f"$ {self.invocation.display()}")
        self._safe_write(
            "[type input above and click Send for prompts. "
            "↵ Enter sends a blank line; ^C sends SIGINT.]"
        )
        env = self._sanitised_env()
        try:
            await self._spawn_pty(env)
        except FileNotFoundError as exc:
            self._safe_write(f"\n[error] could not start ansible-playbook: {exc}")
            self._cleanup_sensitive()
            return
        except OSError as exc:
            self._safe_write(f"\n[error] {exc}")
            self._cleanup_sensitive()
            return

        try:
            # Output streams via the loop reader registered in ``_spawn_pty``;
            # we just wait for the subprocess to exit here.
            assert self._proc is not None
            rc = await self._proc.wait()
            self._safe_write(f"\n[exit {rc}]")
        except asyncio.CancelledError:
            self._terminate_proc()
            raise
        finally:
            # Whatever happens, remove the transient vault password file
            # before anything else can read it.
            self._detach_pty_reader()
            self._close_pty()
            self._cleanup_sensitive()

    async def _spawn_pty(self, env: dict[str, str]) -> None:
        """Spawn ansible-playbook attached to a fresh pseudo-terminal.

        For interactive modules like ``pause`` to work the slave PTY must
        be the *controlling terminal* of the child process, not just its
        stdio. Plain ``start_new_session=True`` detaches the child from
        any controlling tty and Ansible's ``termios.tcgetattr`` calls
        then fail with ENOTTY (errno 25). The proper Linux dance is:

        * ``setsid()`` — new session, no controlling tty.
        * ``ioctl(slave_fd, TIOCSCTTY)`` — make the slave the controlling
          tty of this new session.

        We do both inside ``preexec_fn`` so they run between fork and
        exec in the child. We also set a non-zero window size on the
        master so curses-style modules don't see a 0×0 terminal.
        """
        import pty

        master_fd, slave_fd = pty.openpty()
        # Make the master non-blocking so the loop reader doesn't stall.
        os.set_blocking(master_fd, False)
        # Set a reasonable initial window size so any TUI module on the
        # other side (pause, vars_prompt, etc.) doesn't see 0×0 and bail.
        # struct format is 'HHHH' = (rows, cols, x_pixels, y_pixels).
        try:
            fcntl.ioctl(
                master_fd,
                termios.TIOCSWINSZ,
                struct.pack("HHHH", 40, 120, 0, 0),
            )
        except OSError:
            # Some kernels reject ioctls on master before slave is opened
            # by a process. Non-fatal — we'll fall back to whatever the
            # default is.
            pass

        # Tell the child it's on a real terminal so things like the
        # ``pause`` module's keypress prompt and any colour heuristics
        # do the right thing.
        env = dict(env)
        env.setdefault("TERM", os.environ.get("TERM", "xterm-256color"))

        def _make_controlling_tty() -> None:
            """Run in the child between fork() and execvp().

            * setsid creates a new session with us as the leader and
              detaches from any inherited controlling terminal.
            * TIOCSCTTY then makes the slave PTY our controlling
              terminal — this is what fixes the ENOTTY from pause.
            """
            os.setsid()
            try:
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
            except OSError:
                pass

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self.invocation.argv,
                cwd=str(self.invocation.cwd),
                env=env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                preexec_fn=_make_controlling_tty,
            )
        finally:
            # We don't need the slave end in the parent — the subprocess
            # has it. Close to avoid keeping the PTY open after the
            # child exits.
            os.close(slave_fd)

        self._master_fd = master_fd
        loop = asyncio.get_event_loop()
        loop.add_reader(master_fd, self._on_pty_readable)
        self._reader_attached = True

    def _on_pty_readable(self) -> None:
        """Called by the asyncio loop whenever the PTY master has data."""
        if self._master_fd is None:
            return
        try:
            data = os.read(self._master_fd, 4096)
        except OSError:
            self._detach_pty_reader()
            return
        if not data:
            # EOF — child closed the slave end (process exited).
            self._detach_pty_reader()
            return
        # Buffer raw bytes; emit one decoded line per output line. PTYs
        # use \r\n; we collapse to \n so our log wraps cleanly. Carriage
        # returns mid-line (e.g. progress bars rewriting in place) get
        # swapped for newlines so each repaint shows up as its own
        # entry instead of a perpetually-overwritten one we'd never see.
        self._read_buffer += data
        # Flush complete lines, keeping any trailing partial line for next
        # time so we don't split mid-ANSI-sequence.
        while True:
            nl = self._read_buffer.find(b"\n")
            if nl == -1:
                break
            line, self._read_buffer = (
                self._read_buffer[:nl],
                self._read_buffer[nl + 1 :],
            )
            self._emit_line(line)
        # If the buffer is getting big without a newline (e.g. a very
        # long line, or curses output), flush it periodically.
        if len(self._read_buffer) > 8192:
            self._emit_line(self._read_buffer)
            self._read_buffer = b""

    def _emit_line(self, raw: bytes) -> None:
        text = raw.decode("utf-8", errors="replace").rstrip("\r")
        # Render ANSI escape sequences so colors land in the log instead
        # of showing up as gibberish. Text.from_ansi handles SGR (color)
        # codes and strips terminal-control codes we can't render.
        try:
            self._safe_write(Text.from_ansi(text))
        except Exception:
            # Very rarely Rich barfs on malformed ANSI; fall through to
            # plain text rather than dropping the line entirely.
            self._safe_write(text)

    def _send_input(self, *, newline: bool) -> None:
        if self._master_fd is None:
            return
        inp: Input = self.query_one("#run-input", Input)
        text = inp.value
        payload = text.encode("utf-8") + (b"\n" if newline else b"")
        self._write_to_pty(payload)
        inp.value = ""
        inp.focus()

    def _write_to_pty(self, payload: bytes) -> None:
        if self._master_fd is None:
            return
        if self._proc is not None and self._proc.returncode is not None:
            self.notify("Process already exited", severity="warning")
            return
        try:
            os.write(self._master_fd, payload)
        except OSError as exc:
            self.notify(f"Could not write to process: {exc}", severity="error")

    def _detach_pty_reader(self) -> None:
        if not self._reader_attached or self._master_fd is None:
            return
        try:
            loop = asyncio.get_event_loop()
            loop.remove_reader(self._master_fd)
        except Exception:
            pass
        self._reader_attached = False

    def _close_pty(self) -> None:
        if self._master_fd is None:
            return
        try:
            os.close(self._master_fd)
        except OSError:
            pass
        self._master_fd = None

    def _terminate_proc(self) -> None:
        if self._proc is None or self._proc.returncode is not None:
            return
        try:
            self._proc.terminate()
        except ProcessLookupError:
            pass

    def _safe_write(self, text) -> None:  # noqa: ANN001
        """Write to the run log unless the screen has been unmounted.

        Necessary because the worker task can outlive the screen — the
        user may Esc-back before ansible-playbook has finished printing.
        Writing to a disposed widget raises NoMatches in some Textual
        versions; checking the flag is cheaper and side-effect-free.

        Accepts plain strings or rich ``Text`` instances. Keeps a plain
        mirror so the "save to file" button can write a clean copy
        without ANSI/markup leaking into the file.
        """
        # Mirror plain text first — even when the widget is gone we want
        # to preserve the run output for "save to file" later.
        plain = text.plain if isinstance(text, Text) else str(text)
        self._plain_output.append(plain)
        if self._unmounted:
            return
        try:
            log = self.query_one("#run-log", RichLog)
        except Exception:
            return
        log.write(text)

    def _heading_text(self) -> str:
        return (
            f"[b]{self.play.name}[/b]\n"
            f"[dim]{self.invocation.display()}[/dim]\n"
            f"[dim]cwd: {self.invocation.cwd}[/dim]\n"
            f"[dim]To select text, hold Shift while dragging "
            "(bypasses the TUI's mouse capture). 'Save log…' writes the "
            "full output to a file.[/dim]"
        )

    def _save_log(self) -> None:
        """Dump the plain-text mirror of the run output to a tempfile."""
        if not self._plain_output:
            self.notify("Nothing to save yet", severity="warning")
            return
        try:
            fd, name = tempfile.mkstemp(
                prefix=f"finc-{self.play.name}-",
                suffix=".log",
                dir=str(Path.home()),
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                # ``Path.home()`` keeps the file in a familiar place;
                # mode 0600 isn't strictly needed (no secrets in this
                # output beyond what the user just saw on screen) but
                # match the rest of the app's tempfile conventions.
                fh.write("\n".join(self._plain_output))
                fh.write("\n")
            os.chmod(name, 0o600)
        except OSError as exc:
            self.notify(f"Couldn't save log: {exc}", severity="error", timeout=5)
            return
        # 6-second timeout so the user has time to read the path.
        self.notify(f"Saved to {name}", timeout=6)

    def _sanitised_env(self) -> dict[str, str]:
        """Override colour and buffering settings without losing the user's env.

        We start from the invocation's env (which is a copy of the parent
        process env) and patch a handful of keys. Anything the user set
        explicitly is honoured because we only set defaults via setdefault.

        We deliberately *don't* force colour off here — under a real PTY
        Ansible defaults to colour, and the run output screen renders
        ANSI escape sequences via ``Text.from_ansi``. Setting
        ``ANSIBLE_FORCE_COLOR=1`` in the parent env still works if the
        user wants to be explicit.
        """
        env = dict(self.invocation.env)
        # Ansible's Python control node needs unbuffered stdio for
        # promptly-streamed output even under a PTY.
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("ANSIBLE_NOCOWS", "1")
        return env

    def _cleanup_sensitive(self) -> None:
        if self._cleaned_up:
            return
        self._cleaned_up = True
        runner.cleanup(self.invocation)
