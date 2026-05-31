"""Build and run ``ansible-playbook`` invocations.

Two surface concerns:

* Build a previewable command line we can show the user before running.
* Pass the vault password through ``--vault-password-file`` *without* the
  password ever sitting in a persistent log, env var visible in ``ps``,
  or the user's shell history.

For the second point we write the password to a ``0600`` file on
``/dev/shm`` (tmpfs — never touches persistent storage on Linux), point
ansible-playbook at it via ``--vault-password-file``, and ``unlink`` it
the instant the subprocess exits. The TUI's run-output screen logs only
the constructed argv; it never logs the file's *contents*. As long as the
caller wraps the subprocess in :func:`vault_password_file`, the password
exists on disk for at most the duration of the play run.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class Invocation:
    argv: list[str]
    cwd: Path
    env: dict[str, str]
    """Environment for the subprocess. We deliberately don't put the vault
    password in here — it lives behind ``--vault-password-file`` so it's
    invisible in ``ps`` and child env dumps."""

    sensitive_files: tuple[Path, ...] = field(default_factory=tuple)
    """Paths that need to be unlinked once the subprocess exits.

    The runner caller is responsible for deleting these in a ``finally``
    block; the run-output screen does this automatically.
    """

    def display(self) -> str:
        """Shell-safe-ish single-line preview.

        Safe to log: the password file's path appears here, but its
        contents do not, and the path is gone before the user can ``cat``
        it (it lives on tmpfs and is unlinked when the process exits).
        """
        import shlex

        return " ".join(shlex.quote(a) for a in self.argv)


def build(
    playbook: Path,
    project_root: Path,
    inventory_dir: Path | None = None,
    limit: str | None = None,
    check: bool = False,
    vault_password: str | None = None,
    extra_args: list[str] | None = None,
) -> Invocation:
    """Build an :class:`Invocation` ready to feed asyncio.create_subprocess_exec.

    If ``vault_password`` is given, a transient password file is created on
    tmpfs and added to the invocation's ``sensitive_files`` for cleanup.
    """
    binary = shutil.which("ansible-playbook") or "ansible-playbook"
    argv: list[str] = [binary, str(playbook)]
    if inventory_dir is not None:
        argv += ["-i", str(inventory_dir)]
    if limit:
        argv += ["--limit", limit]
    if check:
        argv.append("--check")

    sensitive: list[Path] = []
    if vault_password is not None:
        pw_path = _write_transient_password(vault_password)
        sensitive.append(pw_path)
        argv += ["--vault-password-file", str(pw_path)]

    if extra_args:
        argv += list(extra_args)

    return Invocation(
        argv=argv,
        cwd=project_root,
        env=dict(os.environ),
        sensitive_files=tuple(sensitive),
    )


def cleanup(invocation: Invocation) -> None:
    """Best-effort removal of any ``sensitive_files``.

    Safe to call multiple times. Failures are swallowed because by the time
    we reach this point the user has already seen the play output and
    cleanup failures are pure plumbing.
    """
    for path in invocation.sensitive_files:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


@contextlib.contextmanager
def vault_password_file(password: str) -> Iterator[Path]:
    """Context manager: yield a 0600 tmpfs path containing ``password``.

    Used by tests; the live runner uses :func:`build` + :func:`cleanup`
    directly so the password file lives across the asyncio subprocess.
    """
    path = _write_transient_password(password)
    try:
        yield path
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _write_transient_password(password: str) -> Path:
    """Write ``password`` to a fresh 0600 file and return its path.

    Prefers ``/dev/shm`` (tmpfs on Linux — never written to persistent
    storage) and falls back to the platform default temp dir if shm
    isn't available. The caller is responsible for unlinking the file
    once the consumer process has exited.
    """
    shm = Path("/dev/shm")
    target_dir: str | None = str(shm) if shm.is_dir() else None
    fd, name = tempfile.mkstemp(prefix="finc-vault-", dir=target_dir)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, password.encode("utf-8"))
        if not password.endswith("\n"):
            os.write(fd, b"\n")
    finally:
        os.close(fd)
    return Path(name)
