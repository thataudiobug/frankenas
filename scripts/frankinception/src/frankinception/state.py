"""Shared mutable state for the running TUI session.

Keeping inventory, host_vars caches, and catalogs on a single object means
screens never reload from disk except when explicitly told to refresh. This
matters because saving comments and key order through ruamel costs more than
PyYAML — we want to load each file once per session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import yaml_io
from .catalogs import Catalog, load_catalogs_for_groups
from .hostvars import HostVars
from .inventory import Inventory
from .paths import Layout


@dataclass
class AppState:
    layout: Layout
    inventory: Inventory
    host_vars_cache: dict[str, HostVars] = field(default_factory=dict)
    docker_catalog_path: Path | None = None
    docker_catalog: dict | None = None
    bind_catalog_path: Path | None = None
    bind_catalog_root: dict | None = None
    """The full YAML doc holding ``docker_bind_catalog`` (so we can save it)."""

    @classmethod
    def load(cls, layout: Layout) -> "AppState":
        inv = Inventory.load(layout.hosts_file)
        state = cls(layout=layout, inventory=inv)
        state._load_docker_catalog()
        state._load_bind_catalog()
        return state

    # ---- host vars ---------------------------------------------------

    def host_vars(self, host: str) -> HostVars:
        if host not in self.host_vars_cache:
            self.host_vars_cache[host] = HostVars.load(self.layout.host_vars_dir, host)
        return self.host_vars_cache[host]

    def catalogs_for(self, host: str) -> list[Catalog]:
        groups = self.inventory.all_groups_of(host)
        return load_catalogs_for_groups(self.layout.group_vars_dir, groups)

    # ---- docker catalog ---------------------------------------------

    def _load_docker_catalog(self) -> None:
        path = self.layout.group_vars_dir / "docker" / "docker_catalog.yml"
        if path.is_file():
            self.docker_catalog_path = path
            self.docker_catalog = yaml_io.load(path) or {}
        else:
            self.docker_catalog_path = path
            self.docker_catalog = None

    def ensure_docker_catalog(self) -> dict:
        """Get the docker catalog doc, creating an empty one if missing."""
        if self.docker_catalog is None:
            self.docker_catalog = yaml_io.empty_map()
            self.docker_catalog["docker_containers_catalog"] = yaml_io.empty_map()
            self.docker_catalog["docker_groups_catalog"] = yaml_io.empty_map()
        return self.docker_catalog

    def save_docker_catalog(self) -> None:
        if self.docker_catalog_path is None or self.docker_catalog is None:
            return
        yaml_io.dump(self.docker_catalog, self.docker_catalog_path)

    # ---- bind catalog -----------------------------------------------

    def _load_bind_catalog(self) -> None:
        # The catalog lives in ``group_vars/all/mounts_catalog.yml`` per repo
        # convention but we'll search in case the user moved it.
        for fpath in self.layout.group_vars_dir.rglob("*.y*ml"):
            data = yaml_io.load(fpath)
            if isinstance(data, dict) and "docker_bind_catalog" in data:
                self.bind_catalog_path = fpath
                self.bind_catalog_root = data
                return
        # Default location if missing.
        self.bind_catalog_path = (
            self.layout.group_vars_dir / "all" / "mounts_catalog.yml"
        )
        self.bind_catalog_root = None

    def bind_catalog(self) -> dict:
        if self.bind_catalog_root is None:
            self.bind_catalog_root = yaml_io.empty_map()
            self.bind_catalog_root["docker_bind_catalog"] = yaml_io.empty_map()
        existing = self.bind_catalog_root.get("docker_bind_catalog")
        if not isinstance(existing, dict):
            self.bind_catalog_root["docker_bind_catalog"] = yaml_io.empty_map()
        return self.bind_catalog_root["docker_bind_catalog"]

    def save_bind_catalog(self) -> None:
        if self.bind_catalog_path is None or self.bind_catalog_root is None:
            return
        yaml_io.dump(self.bind_catalog_root, self.bind_catalog_path)
