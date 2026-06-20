"""Locate the ansible inventory the tool should operate on.

We honour ``ansible.cfg`` so the tool behaves the same as ``ansible-playbook``
invoked from the same directory.
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Layout:
    """Resolved paths for a single inventory."""

    project_root: Path
    """Directory holding ``ansible.cfg`` (or the user-provided root)."""

    inventory_dir: Path
    """Directory holding ``hosts.yml``, ``group_vars/`` and ``host_vars/``."""

    plays_dir: Path
    """Directory holding playbooks (``<project_root>/plays``)."""

    ansible_cfg: Path | None
    """Path to ``ansible.cfg`` if one was found."""

    @property
    def hosts_file(self) -> Path:
        return self.inventory_dir / "hosts.yml"

    @property
    def group_vars_dir(self) -> Path:
        return self.inventory_dir / "group_vars"

    @property
    def host_vars_dir(self) -> Path:
        return self.inventory_dir / "host_vars"

    @property
    def roles_dir(self) -> Path:
        """Directory holding Ansible roles (``<project_root>/roles``)."""
        return self.project_root / "roles"


def _find_ansible_cfg(start: Path) -> Path | None:
    """Walk upward from ``start`` looking for ansible.cfg."""
    for d in [start, *start.parents]:
        candidate = d / "ansible.cfg"
        if candidate.is_file():
            return candidate
    return None


def _expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value)))


def discover(inventory_override: Path | None = None) -> Layout:
    """Resolve the layout we should edit.

    Resolution order:

    1. ``inventory_override`` if given — points directly at the inventory dir.
    2. ``ansible.cfg`` ``[defaults] inventory`` setting, walking up from cwd.
    3. ``./inventories/prod`` if it exists.
    """
    cwd = Path.cwd().resolve()
    cfg_path = _find_ansible_cfg(cwd)

    if inventory_override is not None:
        inv = inventory_override.expanduser().resolve()
        project_root = cfg_path.parent if cfg_path else inv.parent.parent
        plays = project_root / "plays"
        return Layout(
            project_root=project_root,
            inventory_dir=inv,
            plays_dir=plays,
            ansible_cfg=cfg_path,
        )

    if cfg_path is not None:
        parser = configparser.ConfigParser()
        parser.read(cfg_path)
        inv_str = parser.get("defaults", "inventory", fallback="").strip()
        if inv_str:
            inv = _expand(inv_str)
            if not inv.is_absolute():
                inv = (cfg_path.parent / inv).resolve()
            return Layout(
                project_root=cfg_path.parent,
                inventory_dir=inv,
                plays_dir=cfg_path.parent / "plays",
                ansible_cfg=cfg_path,
            )

    fallback = cwd / "inventories" / "prod"
    return Layout(
        project_root=cwd,
        inventory_dir=fallback,
        plays_dir=cwd / "plays",
        ansible_cfg=None,
    )
