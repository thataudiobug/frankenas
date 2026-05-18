"""hosts.yml reader/writer and group-tree operations.

Parses `inventories/{context}/hosts.yml` into a synthetic :class:`GroupNode`
tree, exposes helpers for finding ancestors, flattening the tree for the
dialog checklist, and writing new hosts back to the leaf group while
preserving ruamel.yaml round-trip formatting and comments.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ruamel.yaml import YAML

from frankinception.models import GroupNode

logger = logging.getLogger(__name__)

ROOT_NAME = "__root__"


def load_group_tree(hosts_path: Path) -> GroupNode:
    """Parse ``hosts.yml`` and return the synthetic root :class:`GroupNode`.

    Two-pass build (see design §6.1):

    1. Create a :class:`GroupNode` for every top-level key in the document.
    2. Wire ``parent``/``children`` pointers by walking every ``children:``
       block. Child references that have no top-level definition are
       synthesized as empty leaf nodes and a warning is logged.

    The returned node is a synthetic ``__root__`` whose ``children`` are the
    groups with no parent (the "top" of the user's tree).

    Parameters
    ----------
    hosts_path:
        Path to the Ansible inventory ``hosts.yml`` file.

    Returns
    -------
    GroupNode
        The synthetic ``__root__`` node.
    """

    yaml = YAML(typ="rt")
    with hosts_path.open("r", encoding="utf-8") as fh:
        doc = yaml.load(fh)

    nodes: dict[str, GroupNode] = {}

    # First pass: create a node for every top-level group.
    if doc is not None:
        for name, body in doc.items():
            hosts = {}
            if body is not None:
                raw_hosts = body.get("hosts", None)
                if raw_hosts is not None:
                    hosts = dict(raw_hosts)
            nodes[name] = GroupNode(name=name, hosts=hosts)

    # Second pass: wire parent -> children using `children:` blocks.
    if doc is not None:
        for name, body in doc.items():
            if body is None:
                continue
            if "children" not in body:
                continue
            children_block = body["children"]
            if children_block is None:
                continue
            for child_name in children_block:
                if child_name not in nodes:
                    # Dangling reference: the child is listed under a
                    # parent's `children:` map but never defined at the
                    # top level. Synthesize an empty leaf so selection
                    # still works and warn the operator.
                    logger.warning(
                        "hosts.yml references undefined child group %r "
                        "under parent %r; synthesizing empty leaf",
                        child_name,
                        name,
                    )
                    nodes[child_name] = GroupNode(name=child_name)
                nodes[child_name].parent = nodes[name]
                nodes[name].children[child_name] = nodes[child_name]

    # Everything still parentless hangs off the synthetic root.
    root = GroupNode(name=ROOT_NAME)
    for name, node in nodes.items():
        if node.parent is None:
            node.parent = root
            root.children[name] = node

    return root


def _dfs_find(root: GroupNode, name: str) -> GroupNode | None:
    """Locate a :class:`GroupNode` by name via depth-first search.

    Walks ``root.children`` recursively and returns the first node whose
    ``name`` matches. Returns ``None`` when no such node exists. The
    synthetic root itself is considered a candidate so callers may pass
    either the root returned by :func:`load_group_tree` or any subtree.
    """

    if root.name == name:
        return root
    for child in root.children.values():
        hit = _dfs_find(child, name)
        if hit is not None:
            return hit
    return None


def find_ancestors(tree: GroupNode, leaf_name: str) -> list[str]:
    """Return the ancestor group names of ``leaf_name`` ordered root→parent.

    Walks ``.parent`` pointers from the located node up to (but not
    including) the synthetic ``__root__``. The returned list is ordered
    with the outermost ancestor first and the immediate parent last, and
    excludes both the synthetic root and the leaf itself. See design §6.2.

    Parameters
    ----------
    tree:
        The synthetic root :class:`GroupNode` (or any subtree containing
        ``leaf_name``).
    leaf_name:
        Name of the group whose ancestors are requested.

    Returns
    -------
    list[str]
        Ancestor group names, outermost first.

    Raises
    ------
    ValueError
        If no group named ``leaf_name`` exists anywhere in ``tree``.
    """

    node = _dfs_find(tree, leaf_name)
    if node is None:
        raise ValueError(f"group not found in tree: {leaf_name!r}")

    chain: list[str] = []
    cur = node.parent
    while cur is not None and cur.name != ROOT_NAME:
        chain.insert(0, cur.name)
        cur = cur.parent
    return chain


def flatten_for_checklist(tree: GroupNode) -> list[tuple[str, str, bool]]:
    """Flatten the group tree into rows for the dialog checklist.

    Walks the tree depth-first (children visited in sorted order for
    deterministic output) and emits one row per non-synthetic node as a
    ``(tag, display_text, is_selectable)`` triple:

    - ``tag`` — the bare group name, suitable for the dialog widget.
    - ``display_text`` — the group name indented by two spaces per
      depth level; non-leaf rows are suffixed with ``"  [parent]"`` so
      the operator can distinguish informational rows from pickable
      leaves.
    - ``is_selectable`` — ``True`` only for leaf :class:`GroupNode`
      instances (``is_leaf``). Non-leaf rows are kept for context but
      must not be chosen by the caller.

    The synthetic ``__root__`` node is skipped; its children appear at
    depth 0. See design §9.3.

    Parameters
    ----------
    tree:
        The synthetic root :class:`GroupNode` returned by
        :func:`load_group_tree`.

    Returns
    -------
    list[tuple[str, str, bool]]
        Rows in the order they should be rendered in the checklist.
    """

    rows: list[tuple[str, str, bool]] = []

    def walk(node: GroupNode, depth: int) -> None:
        if node.name != ROOT_NAME:
            indent = "  " * depth
            selectable = node.is_leaf
            label = indent + node.name
            if not selectable:
                label += "  [parent]"
            rows.append((node.name, label, selectable))
        for child_name in sorted(node.children):
            walk(node.children[child_name], depth + 1)

    # Root is virtual; its children render at depth 0.
    walk(tree, -1)
    return rows


def host_exists_anywhere(tree: GroupNode, hostname: str) -> bool:
    """Return ``True`` iff ``hostname`` appears under any group's ``hosts:``.

    Used to enforce the "no duplicate hostnames" rule at the start of the
    create-device flow (see design §9.5 and requirement R4.3). The check
    walks every reachable :class:`GroupNode` — including parent groups
    that happen to carry ``hosts:`` entries directly — and returns as
    soon as a match is found.

    Parameters
    ----------
    tree:
        Any :class:`GroupNode` (typically the synthetic root from
        :func:`load_group_tree`); the search covers the whole subtree.
    hostname:
        Ansible inventory hostname to look for.

    Returns
    -------
    bool
        ``True`` if some group's ``hosts`` mapping contains
        ``hostname``; ``False`` otherwise.
    """

    if hostname in tree.hosts:
        return True
    for child in tree.children.values():
        if host_exists_anywhere(child, hostname):
            return True
    return False


def write_host_to_inventory(
    hosts_path: Path,
    hostname: str,
    leaf_groups: list[str],
) -> None:
    """Insert ``hostname`` into each leaf group's ``hosts:`` map in-place.

    Implements design §6.3 with the strict pre-flight validation from
    R10.4: all leaf groups are verified to exist at the top level of the
    document *before* any mutation is performed, and every target
    ``hosts:`` map is checked for duplicates up front as well. This
    guarantees the write is all-or-nothing — a missing leaf or a
    duplicate hostname raises ``ValueError`` without leaving a partial
    edit on disk.

    The file is loaded in ``ruamel.yaml`` round-trip mode and written
    back with the same YAML instance, so comments, key order, and
    formatting outside the modified ``hosts:`` maps are preserved
    byte-for-byte (property P3). New host bodies are inserted as empty
    :class:`ruamel.yaml.comments.CommentedMap` instances — i.e. the
    hostname is written as a bare key with no sub-mapping — because
    concrete variables live in ``host_vars/{hostname}.yml``, never in
    ``hosts.yml`` (property P1).

    The ``children:`` structure of every group is left untouched; only
    the ``hosts:`` map of each explicitly-named leaf group is modified
    (property P2).

    Parameters
    ----------
    hosts_path:
        Path to the Ansible inventory ``hosts.yml`` file. Must be
        readable and writable.
    hostname:
        Ansible inventory hostname to insert. Written as-is; callers
        are expected to have validated non-emptiness and uniqueness via
        :func:`host_exists_anywhere` prior to calling.
    leaf_groups:
        Non-empty list of leaf group names. Every name must appear as a
        top-level key in the loaded document.

    Raises
    ------
    ValueError
        If any name in ``leaf_groups`` is not defined at the top level
        of the document, or if ``hostname`` already exists in any of
        the target ``hosts:`` maps. In both cases the file is not
        modified.
    """

    # Late import so the module can be imported in environments that
    # don't ship the `comments` submodule eagerly (and to keep the
    # round-trip dependency co-located with the writer).
    from ruamel.yaml.comments import CommentedMap

    yaml = YAML(typ="rt")
    with hosts_path.open("r", encoding="utf-8") as fh:
        doc = yaml.load(fh)

    if doc is None:
        # An empty hosts.yml can't possibly contain any of the
        # requested leaf groups; fail the pre-flight before we try to
        # index into None.
        raise ValueError(
            f"leaf group {leaf_groups[0]!r} not defined at top level"
        )

    # Pre-flight: validate every leaf and every target hosts: map
    # before mutating anything. This is the R10.4 atomicity guarantee.
    for g in leaf_groups:
        if g not in doc:
            raise ValueError(
                f"leaf group {g!r} not defined at top level"
            )
        body = doc[g]
        if body is not None and "hosts" in body:
            existing = body["hosts"]
            if existing is not None and hostname in existing:
                raise ValueError(
                    f"duplicate host: {hostname!r} already in {g!r}"
                )

    # Mutation pass: only reached if every leaf validated cleanly.
    for g in leaf_groups:
        if doc[g] is None:
            doc[g] = CommentedMap()
        if "hosts" not in doc[g] or doc[g]["hosts"] is None:
            doc[g]["hosts"] = CommentedMap()
        doc[g]["hosts"][hostname] = CommentedMap()

    yaml.dump(doc, hosts_path)
