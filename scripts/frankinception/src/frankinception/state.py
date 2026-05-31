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
from .vault import VaultConfig


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

    vault_config: VaultConfig | None = None
    """Authentication info for the secrets vault, populated lazily."""

    @classmethod
    def load(cls, layout: Layout) -> "AppState":
        inv = Inventory.load(layout.hosts_file)
        state = cls(layout=layout, inventory=inv)
        state._load_docker_catalog()
        state._load_bind_catalog()
        state._init_vault_config()
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

    # ---- docker container & group access ---------------------------

    def docker_containers(self) -> dict:
        """The ``docker_containers_catalog`` mapping; created if missing."""
        doc = self.ensure_docker_catalog()
        existing = doc.get("docker_containers_catalog")
        if not isinstance(existing, dict):
            existing = yaml_io.empty_map()
            doc["docker_containers_catalog"] = existing
        return existing

    def docker_groups(self) -> dict:
        """The ``docker_groups_catalog`` mapping; created if missing."""
        doc = self.ensure_docker_catalog()
        existing = doc.get("docker_groups_catalog")
        if not isinstance(existing, dict):
            existing = yaml_io.empty_map()
            doc["docker_groups_catalog"] = existing
        return existing

    def container_groups_for(self, container: str) -> list[str]:
        """Groups whose ``docker_groups_catalog`` membership lists ``container``."""
        out: list[str] = []
        for name, members in self.docker_groups().items():
            if isinstance(members, dict) and container in members:
                out.append(str(name))
        return sorted(out)

    def set_container_groups(self, container: str, groups: list[str]) -> None:
        """Make ``container`` a member of exactly ``groups``.

        Adds/removes the container from each group's mapping. The mapping
        value stays ``None`` (default) when adding — Ansible reads each
        member key-only; non-None values are reserved for per-group
        overrides which the existing UI does not edit.
        """
        target = set(groups)
        for name, members in list(self.docker_groups().items()):
            if not isinstance(members, dict):
                continue
            in_group = container in members
            if in_group and name not in target:
                del members[container]
            elif not in_group and name in target:
                members[container] = None

    def rename_container(self, old: str, new: str) -> None:
        """Rename a container in the catalog and in every group it belongs to.

        Raises ``KeyError`` if ``old`` is missing or ``new`` already exists.
        """
        if old == new:
            return
        containers = self.docker_containers()
        if old not in containers:
            raise KeyError(old)
        if new in containers:
            raise KeyError(f"container '{new}' already exists")
        containers[new] = containers.pop(old)
        for members in self.docker_groups().values():
            if isinstance(members, dict) and old in members:
                members[new] = members.pop(old)

    def delete_container(self, name: str) -> None:
        """Remove ``name`` from the catalog and all docker groups."""
        containers = self.docker_containers()
        containers.pop(name, None)
        for members in self.docker_groups().values():
            if isinstance(members, dict):
                members.pop(name, None)

    def rename_docker_group(self, old: str, new: str) -> None:
        if old == new:
            return
        groups = self.docker_groups()
        if old not in groups:
            raise KeyError(old)
        if new in groups:
            raise KeyError(f"group '{new}' already exists")
        groups[new] = groups.pop(old)

    def delete_docker_group(self, name: str) -> None:
        self.docker_groups().pop(name, None)

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

    # ---- vault -------------------------------------------------------

    @property
    def vault_path(self) -> Path:
        """Conventional location for the project's secrets vault.

        We pick ``group_vars/all/vault.yml`` because Ansible auto-loads it
        for every host without anyone having to wire it up. The user can
        move the file later and we'll pick it up via ansible.cfg.
        """
        return self.layout.group_vars_dir / "all" / "vault.yml"

    def _init_vault_config(self) -> None:
        """Vault password is never persisted across sessions — leave config
        unset so the secrets/play runner screens prompt fresh each time.

        We deliberately ignore any ``vault_password_file`` set in
        ``ansible.cfg`` from outside the tool: the user asked that the
        password live only in process memory, and trusting an on-disk file
        we can't audit would defeat that.
        """
        return
