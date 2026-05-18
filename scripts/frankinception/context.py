"""Inventory context selection and path resolution.

Defines the :class:`InventoryContext` enum (``prod`` / ``test``) and helpers
that resolve the hosts.yml, group_vars, and host_vars paths for the selected
context. Every subsequent operation in a session is scoped to one context.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

# Relative layout under the repo root. ``_discover_base_dir`` walks up from
# the current working directory and from this package's install location
# looking for a directory that contains this marker, so the tool works from
# anywhere inside the repo (e.g. ``~/frankenas/scripts/frankinception``) as
# well as from the repo root itself.
_INVENTORY_MARKER: Path = Path("frankenas") / "inventories"


def _walk_up_for_marker(start: Path) -> Path | None:
    """Walk from ``start`` toward filesystem root looking for the marker.

    Returns the absolute path to ``{ancestor}/frankenas/inventories`` the
    first time that directory exists, or ``None`` if we hit the root
    without finding it.
    """
    start = start.resolve()
    for candidate in (start, *start.parents):
        marker = candidate / _INVENTORY_MARKER
        if marker.is_dir():
            return marker
    return None


def _discover_base_dir() -> Path:
    """Locate ``frankenas/inventories`` by walking up from plausible roots.

    Tries, in order:

    1. The current working directory and its ancestors — covers the
       common case where the operator cd's into any folder inside the
       repo before running ``python -m frankinception``.
    2. This package's install location and its ancestors — covers the
       case where the tool ships alongside the repo (e.g. installed at
       ``~/frankenas/scripts/frankinception/frankinception/``) and was
       invoked from an unrelated working directory.

    Falls back to the literal relative path ``frankenas/inventories``
    (the pre-discovery behaviour) so error reporting downstream still
    produces a path the operator can recognise.
    """
    cwd_hit = _walk_up_for_marker(Path.cwd())
    if cwd_hit is not None:
        return cwd_hit
    pkg_hit = _walk_up_for_marker(Path(__file__).parent)
    if pkg_hit is not None:
        return pkg_hit
    return _INVENTORY_MARKER


# Computed once at import time. Tests redirect this via
# :func:`set_inventory_base_dir`; production callers leave it alone.
_DEFAULT_BASE_DIR: Path = _discover_base_dir()

# Module-level override used by :class:`InventoryContext`'s path properties.
_base_dir: Path = _DEFAULT_BASE_DIR


def set_inventory_base_dir(base_dir: Path | str | None) -> None:
    """Override the base directory used by :class:`InventoryContext` paths.

    Pass ``None`` to restore the default ``frankenas/inventories``. Intended
    for tests that need to point the enum at a temporary fixture tree.
    """
    global _base_dir
    if base_dir is None:
        _base_dir = _DEFAULT_BASE_DIR
    else:
        _base_dir = Path(base_dir)


def get_inventory_base_dir() -> Path:
    """Return the currently active inventory base directory."""
    return _base_dir


class InventoryContext(str, Enum):
    """The two inventory contexts Frankinception operates on.

    The enum value is the directory name under the inventory base
    (``frankenas/inventories`` by default). Path properties resolve
    lazily against :func:`get_inventory_base_dir`, so a test-time
    override via :func:`set_inventory_base_dir` redirects every
    subsequent lookup without touching the enum members themselves.
    """

    PROD = "prod"
    TEST = "test"

    def _resolve_root(self, base_dir: Path | str | None = None) -> Path:
        base = Path(base_dir) if base_dir is not None else get_inventory_base_dir()
        return base / self.value

    @property
    def root(self) -> Path:
        """Path to ``inventories/{context}/`` under the active base dir."""
        return self._resolve_root()

    @property
    def hosts_file(self) -> Path:
        """Path to ``inventories/{context}/hosts.yml``."""
        return self.root / "hosts.yml"

    @property
    def group_vars_dir(self) -> Path:
        """Path to ``inventories/{context}/group_vars/``."""
        return self.root / "group_vars"

    @property
    def host_vars_dir(self) -> Path:
        """Path to ``inventories/{context}/host_vars/``."""
        return self.root / "host_vars"

    def paths_for(self, base_dir: Path | str) -> dict[str, Path]:
        """Resolve all context paths against an explicit ``base_dir``.

        Provided so tests can compute paths against a temp fixture without
        mutating module state via :func:`set_inventory_base_dir`.
        """
        root = self._resolve_root(base_dir)
        return {
            "root": root,
            "hosts_file": root / "hosts.yml",
            "group_vars_dir": root / "group_vars",
            "host_vars_dir": root / "host_vars",
        }
