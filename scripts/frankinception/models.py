"""Shared dataclasses used across frankinception.

Defines :class:`GroupNode` (nodes in the inventory group tree),
:class:`Catalog` (a discovered ``*_catalog`` definition and its entries),
and :class:`HostConfig` (the operator's in-progress selections during the
create-device flow).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GroupNode:
    """A node in the ``hosts.yml`` group tree.

    Leaf groups are those that have no ``children:`` map (they may or
    may not have a ``hosts:`` map). Only leaf groups receive host
    entries.
    """

    name: str
    parent: GroupNode | None = None
    children: dict[str, GroupNode] = field(default_factory=dict)
    hosts: dict[str, dict] = field(default_factory=dict)

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0


@dataclass
class Catalog:
    """One top-level ``x_catalog:`` block discovered in a ``*_catalog.yml`` file."""

    name: str                 # e.g. "docker_containers_catalog"
    enabled_key: str          # e.g. "docker_containers_enabled"
    source_path: Path         # the *_catalog.yml file it came from
    source_scope: str         # "all" or a specific group name
    entries: dict[str, dict]  # raw entries under the x_catalog: key


@dataclass
class HostConfig:
    """The operator's in-progress selections during the create-device flow."""

    hostname: str
    ansible_host: str                          # IP or FQDN
    selected_leaf_groups: list[str]            # lowest-level groups chosen
    selected_ancestor_groups: list[str]        # computed, for display only
    # catalog enabled_key -> list of entry names the user selected
    selected_catalog_entries: dict[str, list[str]] = field(default_factory=dict)
