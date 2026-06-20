"""Discover and read ``*_catalog`` variables.

Catalogs live in two places:

* ``group_vars/`` — cross-cutting catalogs that apply regardless of which
  roles run (e.g. ``users_catalog``, the ``droplet_bind_catalog`` reference
  table). Discovered by :func:`load_catalogs_for_groups`.
* a role's ``defaults/`` or ``vars/`` — role-owned catalogs that only apply
  when that role runs against the host (e.g. ``firewall_catalog`` in
  ``config_firewall``, ``compute_catalog`` in ``provision_proxmox``).
  Discovered by :func:`load_catalogs_for_roles`.

A catalog is any top-level mapping key whose name ends in ``_catalog``. The
tool surfaces these dynamically so new catalogs work without code changes.

Each catalog declares its own selection behaviour with a **marker comment**
written directly on the catalog key — the data describes itself, with no
second source of truth::

    compute_catalog:        # pick one
      small: {cores: 2}
      large: {cores: 6}

    network_catalog:        # pick many
      wan: {...}
      oob: {...}

    docker_bind_catalog:    # reference
      config: {...}

* ``pick one``  → the host-var ``<stem>_enabled`` holds a single key.
* ``pick many`` → it holds many keys (a mapping, or a list if the host
  already uses one).
* ``reference`` → an interpolation table, not a per-host selection; hidden
  from the host editor.

No marker defaults to ``pick one`` (the safe default). The marker lives in a
comment, so it never changes the YAML data Ansible loads — ruamel preserves
it on round-trip. The companion host-var is always ``<stem>_enabled``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from . import yaml_io


class CatalogKind(str, Enum):
    """How the host_vars side of this catalog stores its selection.

    The user only ever declares cardinality (pick one vs pick many) via a
    marker comment in the catalog file. SINGLE maps to "pick one"; MULTI
    maps to "pick many". The on-disk storage form for MULTI (plain list vs
    mapping-with-overrides) is decided at write time to match whatever the
    host already has, defaulting to a mapping for new selections.
    """

    SINGLE = "single"
    """``foo_enabled: "small"`` — exactly one key from the catalog."""

    MULTI = "multi"
    """``foo_enabled: {a:, b:}`` or ``[a, b]`` — any number of keys."""


# Marker comments a catalog author writes to declare cardinality. Matched
# case-insensitively anywhere in the comment attached to the catalog's
# value (inline after the key, or on the line(s) directly below it).
_PICK_ONE_MARKERS = ("pick one", "pick-one", "pickone", "single")
_PICK_MANY_MARKERS = ("pick many", "pick-many", "pickmany", "multi", "multiple")

# Reference-only marker: a table interpolated directly by plays rather than
# selected per-host (e.g. docker_bind_catalog). Hidden from the host editor.
_REFERENCE_MARKERS = ("reference", "reference only", "reference-only", "no-select")


@dataclass(frozen=True)
class CatalogSpec:
    """Resolved treatment for one catalog.

    ``enabled_var`` is the companion host-var (always ``<stem>_enabled``);
    ``kind`` is SINGLE or MULTI; ``reference_only`` hides reference tables
    from the host editor.
    """

    enabled_var: str
    kind: CatalogKind
    reference_only: bool = False


def _spec_for(name: str, kind: CatalogKind, reference_only: bool = False) -> CatalogSpec:
    stem = name.removesuffix("_catalog")
    return CatalogSpec(f"{stem}_enabled", kind, reference_only)


def _marker_from_comment(text: str) -> CatalogSpec | None:
    """Parse a cardinality marker out of a comment string.

    Returns a partial spec (enabled_var filled by the caller) or None if
    no recognised marker is present. Checked in priority order:
    reference-only > pick many > pick one.
    """
    low = text.lower()
    if any(m in low for m in _REFERENCE_MARKERS):
        return CatalogSpec("", CatalogKind.SINGLE, reference_only=True)
    if any(m in low for m in _PICK_MANY_MARKERS):
        return CatalogSpec("", CatalogKind.MULTI)
    if any(m in low for m in _PICK_ONE_MARKERS):
        return CatalogSpec("", CatalogKind.SINGLE)
    return None


def _comment_tokens(value: Any) -> list[str]:
    """Collect comment strings attached to a loaded YAML value.

    ruamel stashes the comment that follows a mapping key on the *value's*
    ``ca.comment``: slot [0] is the inline comment (``key:  # ...``), slot
    [1] is a list of comments on the following indented lines. We read both
    so the author can write the marker either way.
    """
    out: list[str] = []
    ca = getattr(value, "ca", None)
    if ca is None:
        return out
    comment = getattr(ca, "comment", None)
    if not comment:
        return out
    # comment is [inline_token_or_None, [following_tokens] or None]
    inline = comment[0] if len(comment) > 0 else None
    if inline is not None:
        out.append(str(inline.value))
    following = comment[1] if len(comment) > 1 else None
    if following:
        for tok in following:
            if tok is not None:
                out.append(str(tok.value))
    return out


def classify_catalog(name: str, value: Any) -> CatalogSpec:
    """Determine a catalog's spec from its marker comment.

    The catalog declares its own behaviour with a comment:

        compute_catalog:   # pick one
        network_catalog:   # pick many
        docker_bind_catalog:  # reference

    Falls back to SINGLE ("pick one") when no marker is present, which is
    the safe default — a stray multi-write is more surprising than making
    the user add a marker to opt into multi-select.
    """
    for text in _comment_tokens(value):
        partial = _marker_from_comment(text)
        if partial is not None:
            return _spec_for(name, partial.kind, partial.reference_only)
    return _spec_for(name, CatalogKind.SINGLE)


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


def _iter_yaml_files(group_dir: Path) -> Iterable[Path]:
    if not group_dir.is_dir():
        return []
    return sorted(p for p in group_dir.iterdir() if p.suffix in {".yml", ".yaml"})


def load_catalogs_for_groups(
    group_vars_root: Path,
    groups: Iterable[str],
) -> list[Catalog]:
    """Return every selectable ``*_catalog`` exposed by the listed groups.

    Each catalog's behaviour is read from its own marker comment (see
    :func:`classify_catalog`). Catalogs are returned in (group, name) order.
    Reference-only catalogs are skipped since they aren't per-host selections.
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
                spec = classify_catalog(key, value)
                if spec.reference_only:
                    continue
                cat = Catalog(
                    name=key,
                    enabled_var=spec.enabled_var,
                    kind=spec.kind,
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


def _iter_role_var_files(role_dir: Path) -> Iterable[Path]:
    """YAML files under a role's ``defaults/`` and ``vars/``.

    Ansible accepts either ``defaults/main.yml`` or a ``defaults/main/``
    directory whose files are all auto-loaded (same for ``vars/``). We scan
    both forms so a catalog can live in its own file (e.g.
    ``defaults/main/docker_catalog.yml``).
    """
    for sub in ("defaults", "vars"):
        base = role_dir / sub
        main_file = base / "main.yml"
        if main_file.is_file():
            yield main_file
        main_dir = base / "main"
        if main_dir.is_dir():
            for p in sorted(main_dir.iterdir()):
                if p.suffix in {".yml", ".yaml"} and p.is_file():
                    yield p


def load_catalogs_for_roles(
    roles_dir: Path,
    roles: Iterable[str],
) -> list[Catalog]:
    """Return every selectable ``*_catalog`` defined in the listed roles.

    Mirrors :func:`load_catalogs_for_groups` but scans role ``defaults``/
    ``vars`` instead of ``group_vars``. The catalog's ``group`` field is set
    to ``role:<name>`` so the UI can show provenance. Reference-only catalogs
    are skipped.
    """
    seen: dict[tuple[str, str], Catalog] = {}
    for role in roles:
        role_dir = roles_dir / role
        if not role_dir.is_dir():
            continue
        for fpath in _iter_role_var_files(role_dir):
            data = yaml_io.load(fpath)
            if not isinstance(data, dict):
                continue
            for key, value in data.items():
                if not (isinstance(key, str) and key.endswith("_catalog")):
                    continue
                if not isinstance(value, dict):
                    continue
                spec = classify_catalog(key, value)
                if spec.reference_only:
                    continue
                cat = Catalog(
                    name=key,
                    enabled_var=spec.enabled_var,
                    kind=spec.kind,
                    group=f"role:{role}",
                    source_file=fpath,
                    entries=dict(value),
                )
                seen.setdefault((role, key), cat)
    return [seen[k] for k in sorted(seen.keys())]
