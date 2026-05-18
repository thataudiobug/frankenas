"""host_vars file writer.

Builds and writes `inventories/{context}/host_vars/{hostname}.yml` from the
operator's selections. Converts each selected ``foo_catalog`` entry into an
empty-valued key under the matching ``foo_enabled:`` dict, plus the
``ansible_host`` connection target.
"""

from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from .context import InventoryContext
from .models import Catalog, HostConfig


def build_host_vars_doc(
    host: HostConfig,
    catalogs: list[Catalog],
) -> CommentedMap:
    """Build the ordered host_vars mapping ready to be dumped as YAML.

    The returned document always has ``ansible_host`` as its first key,
    followed by one ``enabled_key`` mapping per catalog the operator
    selected at least one entry from. Catalogs with zero selections are
    omitted entirely (no empty blocks appear in the output). Catalog
    order in the output mirrors the discovery order of ``catalogs``.

    Each selected entry becomes a null-valued key under its
    ``enabled_key`` mapping, which ruamel.yaml renders as a bare
    ``entry_name:`` line with no value.
    """
    doc: CommentedMap = CommentedMap()
    doc["ansible_host"] = host.ansible_host

    for catalog in catalogs:
        picks = host.selected_catalog_entries.get(catalog.enabled_key, [])
        if not picks:
            continue
        block: CommentedMap = CommentedMap()
        for entry_name in picks:
            block[entry_name] = None
        doc[catalog.enabled_key] = block

    return doc


def write_host_vars(
    context: InventoryContext,
    host: HostConfig,
    catalogs: list[Catalog],
    *,
    overwrite: bool = False,
) -> Path:
    """Write the operator's selections to ``host_vars/{hostname}.yml``.

    Computes the target path under ``context.host_vars_dir`` and refuses
    to clobber an existing file unless ``overwrite`` is True, in which
    case the caller has already confirmed the overwrite with the
    operator. The ``host_vars`` directory is created if missing so the
    very first host in a brand-new inventory works without manual setup.

    The YAML is dumped via ``ruamel.yaml`` round-trip mode with
    block-style flow, 2-space mapping indent, and 4/2 sequence indent to
    match the rest of the inventory's house style.
    """
    target = context.host_vars_dir / (host.hostname + ".yml")
    if target.exists() and not overwrite:
        raise FileExistsError(target)
    context.host_vars_dir.mkdir(parents=True, exist_ok=True)
    doc = build_host_vars_doc(host, catalogs)
    yaml = YAML(typ="rt")
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.dump(doc, target)
    return target
