"""Discover and read ``*_catalog`` variables from ``group_vars/``.

A catalog is any top-level mapping key in a ``group_vars/<group>/*.yml`` file
whose name ends in ``_catalog``. The tool surfaces these dynamically so new
catalogs work without code changes.

For each catalog we also infer the *enabled* host-var name. The convention
used in this repo is ``foo_catalog`` paired with ``foo_enabled`` in
``host_vars/<host>.yml``. We also handle the special docker pair
``docker_groups_catalog`` <-> ``docker_groups_enabled`` (whose value is a
mapping rather than a scalar).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from . import yaml_io


class CatalogKind(str, Enum):
    """How the host_vars side of this catalog stores its selection."""

    SCALAR = "scalar"
    """``foo_enabled: "small"`` — single key from the catalog."""

    LIST = "list"
    """``users_enabled: [casey, robot]`` — multiple keys."""

    MAPPING = "mapping"
    """``docker_groups_enabled: {public:, piracy:}`` — multiple keys w/ option for nested overrides."""


# Catalogs that don't follow the simple ``foo_catalog -> foo_enabled`` rule.
# Mapped to (enabled_var_name, kind).
_OVERRIDES: dict[str, tuple[str, CatalogKind]] = {
    "docker_groups_catalog": ("docker_groups_enabled", CatalogKind.MAPPING),
    "docker_containers_catalog": ("docker_containers_enabled", CatalogKind.MAPPING),
    "users_catalog": ("users_enabled", CatalogKind.LIST),
}


@dataclass
class Catalog:
    """A single catalog discovered for a host's group set."""

    name: str
    """The catalog variable name (e.g. ``compute_catalog``)."""

    enabled_var: str
    """Companion host_vars key (e.g. ``compute_enabled``)."""

    kind: CatalogKind
    """How a selection is stored in host_vars."""

    group: str
    """Group whose ``group_vars`` defines this catalog."""

    source_file: Path
    """File the catalog was read from."""

    entries: dict[str, Any] = field(default_factory=dict)
    """Raw catalog body — keys are the choices, values are the metadata."""

    @property
    def display_name(self) -> str:
        """Human label, e.g. ``compute_catalog`` -> ``Compute``."""
        stem = self.name.removesuffix("_catalog")
        return stem.replace("_", " ").title() or self.name


def _classify(name: str) -> tuple[str, CatalogKind]:
    if name in _OVERRIDES:
        return _OVERRIDES[name]
    stem = name.removesuffix("_catalog")
    return f"{stem}_enabled", CatalogKind.SCALAR


def _iter_yaml_files(group_dir: Path) -> Iterable[Path]:
    if not group_dir.is_dir():
        return []
    return sorted(p for p in group_dir.iterdir() if p.suffix in {".yml", ".yaml"})


def load_catalogs_for_groups(
    group_vars_root: Path, groups: Iterable[str]
) -> list[Catalog]:
    """Return every ``*_catalog`` exposed by the listed groups (and ``all``).

    Catalogs are returned in (group, name) order. Duplicate names across
    groups are kept — the UI can decide which to show, but in practice the
    repo doesn't repeat them.
    """
    seen: dict[tuple[str, str], Catalog] = {}
    # The 'all' group always applies.
    target_groups = ["all", *list(groups)]
    for group in target_groups:
        group_dir = group_vars_root / group
        # group_vars also accepts <group>.yml as a sibling file.
        candidates: list[Path] = list(_iter_yaml_files(group_dir))
        single = group_vars_root / f"{group}.yml"
        if single.is_file():
            candidates.append(single)
        for fpath in candidates:
            data = yaml_io.load(fpath)
            if not isinstance(data, dict):
                continue
            for key, value in data.items():
                if not (isinstance(key, str) and key.endswith("_catalog")):
                    continue
                if not isinstance(value, dict):
                    continue
                enabled_var, kind = _classify(key)
                cat = Catalog(
                    name=key,
                    enabled_var=enabled_var,
                    kind=kind,
                    group=group,
                    source_file=fpath,
                    entries=dict(value),
                )
                # Earlier files win if the same catalog appears twice.
                seen.setdefault((group, key), cat)
    return [seen[k] for k in sorted(seen.keys())]


def find_catalog(catalogs: Iterable[Catalog], name: str) -> Catalog | None:
    for c in catalogs:
        if c.name == name:
            return c
    return None
