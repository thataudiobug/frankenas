"""Catalog discovery and parsing.

Walks `group_vars/all/` and `group_vars/{group}/` for files matching
``*_catalog.yml``, extracts every top-level ``*_catalog:`` key, and returns
:class:`Catalog` records that the create-device flow presents as multi-select
dialogs. Catalog files are read-only from frankinception's perspective.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ruamel.yaml import YAML, YAMLError

from frankinception.context import InventoryContext
from frankinception.models import Catalog

logger = logging.getLogger(__name__)


def _dedup_keep_order(values: list[str]) -> list[str]:
    """Return ``values`` with duplicates removed, preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def discover_catalogs(
    context: InventoryContext,
    groups: list[str],
) -> list[Catalog]:
    """Discover every ``*_catalog`` block in scope for ``groups``.

    Scopes are visited in this order (design §7.1, R7.6):

    1. ``group_vars/all/`` — always consulted.
    2. ``group_vars/{g}/`` for each ``g`` in ``groups``, after
       dedup-keep-order.

    Within a scope, files are walked in sorted filename order. Each
    discovered top-level key whose name ends in ``_catalog`` and whose
    value is a YAML mapping yields one :class:`Catalog`. Missing
    per-group directories are silently skipped (R7.4). Files that fail
    to parse as YAML are logged at WARNING level and skipped, so one
    bad file never aborts discovery (R7.5).
    """
    yaml = YAML(typ="safe")
    catalogs: list[Catalog] = []

    scopes: list[tuple[str, Path]] = [
        ("all", context.group_vars_dir / "all"),
    ]
    for g in _dedup_keep_order(groups):
        scopes.append((g, context.group_vars_dir / g))

    for scope_name, scope_dir in scopes:
        if not scope_dir.is_dir():
            continue
        for file in sorted(scope_dir.glob("*_catalog.yml")):
            try:
                doc = yaml.load(file)
            except YAMLError as exc:
                logger.warning("skipping malformed catalog %s: %s", file, exc)
                continue
            if doc is None or not isinstance(doc, dict):
                continue
            for key, body in doc.items():
                if not isinstance(key, str) or not key.endswith("_catalog"):
                    continue
                if not isinstance(body, dict):
                    continue
                enabled_key = key.removesuffix("_catalog") + "_enabled"
                catalogs.append(
                    Catalog(
                        name=key,
                        enabled_key=enabled_key,
                        source_path=file,
                        source_scope=scope_name,
                        entries=body,
                    )
                )

    return catalogs
