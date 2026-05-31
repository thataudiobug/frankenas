"""Ansible Vault read/write helpers.

We shell out to ``ansible-vault`` rather than importing ``ansible-core``
directly, so the tool tracks whatever Ansible version is installed on the
control node without us pinning a specific release.

Two file shapes show up in practice:

1. A whole YAML file encrypted in one go via ``ansible-vault encrypt``.
   The file starts with ``$ANSIBLE_VAULT;1.1;AES256`` followed by hex
   ciphertext. We use this shape.

2. Individual values encrypted with ``encrypt_string`` and pasted into a
   normal YAML file (``!vault |`` blocks). More flexible, more error-prone.
   We don't generate this shape but ``ansible-playbook`` consumes it
   natively, so the user can mix in hand-edited values.

This module always reads/writes the whole-file shape — it's simpler and
matches the typical "everything-secret-in-one-vault.yml" workflow.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Any

from . import yaml_io


VAULT_HEADER = "$ANSIBLE_VAULT"


class VaultError(RuntimeError):
    """Raised for any vault read/write failure with a user-friendly message."""


def is_encrypted(path: Path) -> bool:
    """True if ``path`` exists and looks like a vault-encrypted file."""
    if not path.is_file():
        return False
    with path.open("rb") as fh:
        head = fh.read(len(VAULT_HEADER))
    return head == VAULT_HEADER.encode("ascii")


def vault_binary() -> str:
    """Locate ``ansible-vault`` or raise."""
    binary = shutil.which("ansible-vault")
    if binary is None:
        raise VaultError(
            "ansible-vault not found on PATH. Install ansible-core or ansible."
        )
    return binary


@dataclass
class VaultConfig:
    """How we authenticate to the vault for this session.

    Either ``password_file`` is set (preferred — matches ``ansible.cfg``)
    or ``password`` holds the plaintext password in memory only.
    """

    password_file: Path | None = None
    password: str | None = None

    def as_args(self) -> list[str]:
        """Args to splice into an ``ansible-vault`` invocation."""
        if self.password_file is not None:
            return ["--vault-password-file", str(self.password_file)]
        # When using an in-memory password we hand it to ansible-vault via a
        # transient password file — passing on the CLI would leak it into
        # ``ps``. The transient file is created per call so the temp path is
        # cleaned up immediately.
        return []  # callers must call ``with self.transient_password_file()``

    def needs_transient_file(self) -> bool:
        return self.password_file is None and self.password is not None


def _run_vault(args: list[str], cfg: VaultConfig, *, stdin: str | None = None) -> str:
    """Run ``ansible-vault`` with the right password plumbing.

    Returns stdout as text. Raises VaultError on non-zero exit, surfacing
    stderr in the message.
    """
    if cfg.needs_transient_file():
        # Write the password to a 0600 temp file we delete immediately after.
        # Done this way (rather than echo on stdin) because ansible-vault
        # decides on its password source from CLI flags, not from stdin.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False, prefix="finc-vault-"
        ) as fh:
            os.fchmod(fh.fileno(), 0o600)
            fh.write(cfg.password or "")
            tmp = Path(fh.name)
        try:
            full_args = [vault_binary(), *args, "--vault-password-file", str(tmp)]
            return _execute(full_args, stdin=stdin)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
    elif cfg.password_file is not None:
        full_args = [vault_binary(), *args, "--vault-password-file", str(cfg.password_file)]
        return _execute(full_args, stdin=stdin)
    else:
        raise VaultError("No vault password configured")


def _execute(argv: list[str], stdin: str | None) -> str:
    proc = subprocess.run(
        argv,
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise VaultError(
            f"{argv[0]} failed (exit {proc.returncode}): "
            f"{proc.stderr.strip() or proc.stdout.strip() or '(no output)'}"
        )
    return proc.stdout


# ---- whole-file vault read/write --------------------------------------------


def load_vault_yaml(path: Path, cfg: VaultConfig) -> dict[str, Any]:
    """Decrypt ``path`` and parse it as YAML.

    Returns ``{}`` if the file doesn't exist yet (so a fresh vault is just
    "an empty mapping"). Raises VaultError on decryption or parse failure.
    """
    if not path.exists():
        return {}
    if not is_encrypted(path):
        # Allow editing a still-plaintext vault file the first time around.
        # Anything written back will be encrypted regardless.
        text = path.read_text(encoding="utf-8")
    else:
        text = _run_vault(["view", str(path)], cfg)
    if not text.strip():
        return {}
    data = yaml_io.make_yaml().load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise VaultError(
            f"vault at {path} doesn't contain a top-level mapping; "
            "frankinception expects a YAML dict of secret name → value."
        )
    return dict(data)


def save_vault_yaml(path: Path, data: dict[str, Any], cfg: VaultConfig) -> None:
    """Serialise ``data`` to YAML and write it as a vault-encrypted file.

    The whole file is replaced atomically: we encrypt to a temp file in the
    same directory, then ``rename`` over the destination. If the destination
    already exists and was encrypted, the same algorithm/version is used by
    ``ansible-vault encrypt``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = StringIO()
    yaml_io.make_yaml().dump(data, buf)
    plaintext = buf.getvalue()
    if not plaintext.endswith("\n"):
        plaintext += "\n"

    # Write plaintext to a temp file, then encrypt it in place.
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=".vault-",
        suffix=".tmp",
        delete=False,
    ) as fh:
        os.fchmod(fh.fileno(), 0o600)
        fh.write(plaintext)
        tmp_path = Path(fh.name)
    try:
        _run_vault(["encrypt", str(tmp_path)], cfg)
        # ``encrypt`` modifies the file in place; now move it into position.
        os.replace(tmp_path, path)
    except VaultError:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass
        raise


# ---- ansible.cfg helpers ----------------------------------------------------
#
# Intentionally absent: helpers to read/write ``vault_password_file`` in
# ``ansible.cfg``. The tool's contract with the user is that the vault
# password lives only in process memory for the current session — never
# on disk. Encouraging an on-disk password file would defeat that.
