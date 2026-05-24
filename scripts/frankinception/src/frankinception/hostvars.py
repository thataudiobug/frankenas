"""Read and edit ``host_vars/<host>.yml`` while preserving formatting."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml.comments import CommentedMap, CommentedSeq

from . import yaml_io
from .catalogs import Catalog, CatalogKind


@dataclass
class HostVars:
    path: Path
    raw: CommentedMap = field(default_factory=yaml_io.empty_map)

    @classmethod
    def load(cls, host_vars_dir: Path, host: str) -> "HostVars":
        path = host_vars_dir / f"{host}.yml"
        data = yaml_io.load(path)
        if data is None:
            data = yaml_io.empty_map()
        return cls(path=path, raw=data)

    def save(self) -> None:
        yaml_io.dump(self.raw, self.path)

    # ---- generic accessors ------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.raw[key] = value

    def unset(self, key: str) -> None:
        if key in self.raw:
            del self.raw[key]

    # ---- catalog-aware helpers --------------------------------------

    def selection(self, catalog: Catalog) -> Any:
        """Return whatever the host_vars side currently holds.

        Type depends on ``catalog.kind``: scalar string, list of strings, or
        mapping of name -> overrides.
        """
        return self.raw.get(catalog.enabled_var)

    def selected_keys(self, catalog: Catalog) -> list[str]:
        """Always return a flat list of selected catalog keys."""
        sel = self.selection(catalog)
        if sel is None:
            return []
        if catalog.kind is CatalogKind.SCALAR:
            return [str(sel)] if sel else []
        if catalog.kind is CatalogKind.LIST:
            return [str(k) for k in sel] if isinstance(sel, list) else []
        # MAPPING
        if isinstance(sel, dict):
            return list(sel.keys())
        return []

    def set_scalar(self, catalog: Catalog, value: str | None) -> None:
        if value is None or value == "":
            self.unset(catalog.enabled_var)
        else:
            self.raw[catalog.enabled_var] = value

    def set_list(self, catalog: Catalog, values: list[str]) -> None:
        seq = yaml_io.empty_seq()
        for v in values:
            seq.append(v)
        if seq:
            self.raw[catalog.enabled_var] = seq
        else:
            # Match the existing repo style: keep the key with an empty value
            # rather than removing it entirely. Users sometimes leave an
            # empty placeholder block. We remove only if the key isn't there.
            self.raw[catalog.enabled_var] = seq

    def set_mapping(self, catalog: Catalog, keys: list[str]) -> None:
        """For mapping-style catalogs, write ``{key: ~, ...}``.

        Existing per-key overrides for surviving keys are preserved; entries
        for removed keys are dropped.
        """
        existing = self.raw.get(catalog.enabled_var)
        merged = yaml_io.empty_map()
        if isinstance(existing, dict):
            for k in keys:
                if k in existing:
                    merged[k] = existing[k]
                else:
                    merged[k] = None
        else:
            for k in keys:
                merged[k] = None
        self.raw[catalog.enabled_var] = merged

    # ---- container-override helpers ---------------------------------

    def container_overrides(self) -> CommentedMap:
        """The ``docker_containers_overrides`` mapping, created if needed."""
        existing = self.raw.get("docker_containers_overrides")
        if isinstance(existing, dict):
            return existing
        new = yaml_io.empty_map()
        self.raw["docker_containers_overrides"] = new
        return new

    def set_container_override(self, container: str, key: str, value: Any) -> None:
        overrides = self.container_overrides()
        body = overrides.get(container)
        if not isinstance(body, dict):
            body = yaml_io.empty_map()
            overrides[container] = body
        if value is None:
            if key in body:
                del body[key]
        else:
            body[key] = value


def list_known_hosts(host_vars_dir: Path) -> list[str]:
    if not host_vars_dir.is_dir():
        return []
    return sorted(
        p.stem for p in host_vars_dir.iterdir() if p.suffix in {".yml", ".yaml"}
    )
