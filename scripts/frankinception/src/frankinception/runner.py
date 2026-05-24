"""Build ``ansible-playbook`` command lines.

The TUI shells out to the system's ``ansible-playbook``; we don't import
ansible. Keeping the command construction in one place makes it trivial to
preview before executing.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Invocation:
    argv: list[str]
    cwd: Path
    env: dict[str, str]

    def display(self) -> str:
        """Shell-safe-ish single-line preview."""
        import shlex

        return " ".join(shlex.quote(a) for a in self.argv)


def build(
    playbook: Path,
    project_root: Path,
    inventory_dir: Path | None = None,
    limit: str | None = None,
    check: bool = False,
    extra_args: list[str] | None = None,
) -> Invocation:
    binary = shutil.which("ansible-playbook") or "ansible-playbook"
    argv: list[str] = [binary, str(playbook)]
    if inventory_dir is not None:
        argv += ["-i", str(inventory_dir)]
    if limit:
        argv += ["--limit", limit]
    if check:
        argv.append("--check")
    if extra_args:
        argv += list(extra_args)
    return Invocation(argv=argv, cwd=project_root, env=dict(os.environ))
