"""Read and edit ``hosts.yml``.

Ansible's inventory grammar lets a group either declare ``hosts:`` directly,
or declare ``children:`` referencing other groups (which themselves may have
hosts or more children). A host belongs to a group transitively if the group
appears anywhere on the path from a top-level group to that host's leaf.

This module flattens that into convenient lookups while still letting us
write back to ``hosts.yml`` without touching the original layout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from ruamel.yaml.comments import CommentedMap

from . import yaml_io


@dataclass
class Inventory:
    """In-memory view of ``hosts.yml`` with edit helpers."""

    path: Path
    raw: CommentedMap = field(default_factory=yaml_io.empty_map)
    """The round-trippable YAML tree we will write back."""

    @classmethod
    def load(cls, path: Path) -> "Inventory":
        data = yaml_io.load(path)
        if data is None:
            data = yaml_io.empty_map()
        return cls(path=path, raw=data)

    def save(self) -> None:
        yaml_io.dump(self.raw, self.path)

    # ---- traversal ---------------------------------------------------

    def groups(self) -> list[str]:
        """All declared group names, in declaration order."""
        return list(self._all_group_nodes().keys())

    def hosts(self) -> list[str]:
        """All host names, sorted, deduplicated."""
        seen: set[str] = set()
        for node in self._all_group_nodes().values():
            hosts = node.get("hosts") if isinstance(node, dict) else None
            if isinstance(hosts, dict):
                seen.update(hosts.keys())
        return sorted(seen)

    def host_node(self, host: str) -> CommentedMap | None:
        """The hosts-block entry for ``host`` (may carry inline vars)."""
        for node in self._all_group_nodes().values():
            hosts = node.get("hosts") if isinstance(node, dict) else None
            if isinstance(hosts, dict) and host in hosts:
                return hosts[host]
        return None

    def direct_groups_of(self, host: str) -> list[str]:
        """Groups that directly list ``host`` under ``hosts:``."""
        out = []
        for name, node in self._all_group_nodes().items():
            hosts = node.get("hosts") if isinstance(node, dict) else None
            if isinstance(hosts, dict) and host in hosts:
                out.append(name)
        return out

    def all_groups_of(self, host: str) -> list[str]:
        """All groups including parents reached via ``children:``.

        Uses the parent map so groups defined inline under another group's
        ``children:`` block contribute their ancestry too.
        """
        parents = self._parent_map()
        out: set[str] = set(self.direct_groups_of(host))
        frontier = list(out)
        while frontier:
            g = frontier.pop()
            for parent in parents.get(g, ()):  # noqa: PERF203
                if parent not in out:
                    out.add(parent)
                    frontier.append(parent)
        return sorted(out)

    # ---- editing -----------------------------------------------------

    def add_host_to_group(self, host: str, group: str) -> None:
        node = self._ensure_group(group)
        hosts = node.get("hosts")
        if not isinstance(hosts, dict):
            hosts = yaml_io.empty_map()
            node["hosts"] = hosts
        if host not in hosts:
            hosts[host] = None

    def remove_host_from_group(self, host: str, group: str) -> None:
        node = self._find_group_node(group)
        if not isinstance(node, dict):
            return
        hosts = node.get("hosts")
        if isinstance(hosts, dict) and host in hosts:
            del hosts[host]
            if not hosts:
                del node["hosts"]

    def remove_host(self, host: str) -> None:
        for group in self.direct_groups_of(host):
            self.remove_host_from_group(host, group)

    # ---- internals ---------------------------------------------------

    def _ensure_group(self, group: str) -> CommentedMap:
        existing = self._find_group_node(group)
        if isinstance(existing, dict):
            return existing
        # New groups go at the top level for simplicity. Users who want a
        # group nested under another group's children can do that by hand.
        new = yaml_io.empty_map()
        self.raw[group] = new
        return new

    def _find_group_node(self, group: str) -> CommentedMap | None:
        return self._all_group_nodes().get(group)

    def _all_group_nodes(self) -> dict[str, CommentedMap]:
        """Every group node anywhere in the tree, keyed by group name.

        Ansible YAML inventories let groups be declared either at the top
        level or inline under another group's ``children:`` mapping. The
        first occurrence wins (matches Ansible's own behaviour: writing back
        edits the canonical declaration).
        """
        out: dict[str, CommentedMap] = {}

        def walk(name: str, node: Any) -> None:
            if not isinstance(node, dict):
                return
            out.setdefault(name, node)
            children = node.get("children")
            if isinstance(children, dict):
                for child_name, child_node in children.items():
                    walk(child_name, child_node)

        for name, node in self.raw.items():
            walk(name, node)
        return out

    def _parent_map(self) -> dict[str, list[str]]:
        """Map group name -> list of groups that declare it as a child."""
        parents: dict[str, list[str]] = {}
        for name, node in self._all_group_nodes().items():
            children = node.get("children") if isinstance(node, dict) else None
            if isinstance(children, dict):
                for child_name in children.keys():
                    parents.setdefault(child_name, []).append(name)
        return parents


def list_assignable_groups(inv: Inventory) -> list[str]:
    """Groups a host can sensibly be added to via the UI.

    We exclude purely-organizational groups whose only role is to bundle
    children (no ``hosts:`` block of their own and no children that take
    hosts directly). These should still be visible as inherited ancestry,
    just not toggleable.
    """
    out: list[str] = []
    for name, node in inv._all_group_nodes().items():  # noqa: SLF001
        if not isinstance(node, dict):
            continue
        if "hosts" in node or "children" not in node:
            out.append(name)
    return sorted(out)


def host_groups_summary(inv: Inventory, host: str) -> dict[str, Iterable[str]]:
    """Convenience for the UI: split direct vs inherited groups."""
    direct = inv.direct_groups_of(host)
    all_ = inv.all_groups_of(host)
    return {"direct": direct, "inherited": [g for g in all_ if g not in direct]}
