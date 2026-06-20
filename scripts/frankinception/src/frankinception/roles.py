"""Resolve which roles apply to a host, by reading the playbooks.

Catalogs moved from ``group_vars/`` into the owning role's ``defaults/`` (or
``vars/``) — so to know which catalogs a host can select, we must know which
roles run against that host. Ansible expresses that through playbooks:

    - hosts: <group>
      roles:
        - role: provision_proxmox

A role can also pull in other roles at runtime via ``include_role`` /
``import_role`` in its task files, or declaratively via ``dependencies`` in
``meta/main.yml``. We follow all three so a catalog owned by an
included/depended role (e.g. ``config_firewall``, included by
``provision_proxmox``) is still discovered for the host.

The mapping we build is: host -> groups -> plays targeting those groups ->
roles named there -> (transitively) included/depended roles.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ruamel.yaml import YAML

# include_role / import_role with a `name:` on the following lines, or inline.
_INCLUDE_RE = re.compile(
    r"(?:ansible\.builtin\.)?(?:include_role|import_role)\b", re.IGNORECASE
)
_NAME_RE = re.compile(r"^\s*name:\s*([A-Za-z0-9_./-]+)\s*$")


def _safe_load_all(text: str) -> list:
    yaml = YAML(typ="safe")
    try:
        docs = list(yaml.load_all(text))
    except Exception:
        return []
    out: list = []
    for d in docs:
        if isinstance(d, list):
            out.extend(d)
    return out


def _roles_in_play(play: dict) -> list[str]:
    """Role names listed in a play's ``roles:`` block."""
    out: list[str] = []
    roles = play.get("roles")
    if not isinstance(roles, list):
        return out
    for entry in roles:
        if isinstance(entry, str):
            out.append(entry)
        elif isinstance(entry, dict):
            name = entry.get("role") or entry.get("name")
            if isinstance(name, str):
                out.append(name)
    return out


def _hosts_token(play: dict) -> str:
    h = play.get("hosts")
    if isinstance(h, str):
        return h
    if isinstance(h, list):
        return ",".join(str(x) for x in h)
    return ""


@dataclass
class PlayRoleMap:
    """group token -> set of role names named directly in plays targeting it."""

    by_group: dict[str, set[str]]

    def roles_for_groups(self, groups: Iterable[str]) -> set[str]:
        wanted = set(groups)
        out: set[str] = set()
        for token, roles in self.by_group.items():
            # A play's hosts: token can be a group name, a comma list, or
            # 'all'. Match if any of the host's groups appears in the token,
            # or the token targets everything.
            parts = {p.strip() for p in token.split(",") if p.strip()}
            if "all" in parts or (parts & wanted):
                out |= roles
        return out


def build_play_role_map(plays_dir: Path) -> PlayRoleMap:
    """Scan every playbook and map each play's ``hosts:`` token to its roles."""
    by_group: dict[str, set[str]] = {}
    if not plays_dir.is_dir():
        return PlayRoleMap(by_group)
    for path in sorted(plays_dir.iterdir()):
        if path.suffix not in {".yml", ".yaml"} or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for play in _safe_load_all(text):
            if not isinstance(play, dict):
                continue
            token = _hosts_token(play)
            roles = _roles_in_play(play)
            if token and roles:
                by_group.setdefault(token, set()).update(roles)
    return PlayRoleMap(by_group)


def _included_roles_in_tasks(role_dir: Path) -> set[str]:
    """Role names pulled in via include_role/import_role in a role's tasks."""
    out: set[str] = set()
    tasks_dir = role_dir / "tasks"
    if not tasks_dir.is_dir():
        return out
    for tf in tasks_dir.rglob("*.y*ml"):
        try:
            lines = tf.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for i, line in enumerate(lines):
            if not _INCLUDE_RE.search(line):
                continue
            # The name: may be inline-ish on following lines within the block.
            for probe in lines[i + 1 : i + 6]:
                m = _NAME_RE.match(probe)
                if m:
                    out.add(m.group(1))
                    break
    return out


def _dependency_roles(role_dir: Path) -> set[str]:
    """Role names declared in meta/main.yml ``dependencies``."""
    out: set[str] = set()
    meta = role_dir / "meta" / "main.yml"
    if not meta.is_file():
        return out
    yaml = YAML(typ="safe")
    try:
        data = yaml.load(meta.read_text(encoding="utf-8"))
    except Exception:
        return out
    if not isinstance(data, dict):
        return out
    deps = data.get("dependencies")
    if not isinstance(deps, list):
        return out
    for dep in deps:
        if isinstance(dep, str):
            out.add(dep)
        elif isinstance(dep, dict):
            name = dep.get("role") or dep.get("name")
            if isinstance(name, str):
                out.add(name)
    return out


def expand_roles(roles: Iterable[str], roles_dir: Path) -> set[str]:
    """Transitively expand a role set with included + depended roles."""
    resolved: set[str] = set()
    frontier = list(roles)
    while frontier:
        name = frontier.pop()
        if name in resolved:
            continue
        resolved.add(name)
        role_dir = roles_dir / name
        if not role_dir.is_dir():
            continue
        for nxt in _included_roles_in_tasks(role_dir) | _dependency_roles(role_dir):
            if nxt not in resolved:
                frontier.append(nxt)
    return resolved


def roles_for_host(
    groups: Iterable[str],
    plays_dir: Path,
    roles_dir: Path,
) -> set[str]:
    """All roles that run against a host, transitively expanded."""
    play_map = build_play_role_map(plays_dir)
    direct = play_map.roles_for_groups(groups)
    return expand_roles(direct, roles_dir)
