"""Create-device flow.

The one fully-implemented flow: walks the operator through picking leaf
groups, entering hostname + ansible_host, selecting catalog entries from every
applicable ``*_catalog.yml``, then writes both ``hosts.yml`` and the new
``host_vars/{hostname}.yml``. See design §9.5 for orchestration.
"""

from __future__ import annotations

from typing import Any

from ..catalogs import discover_catalogs
from ..context import InventoryContext
from ..host_vars import write_host_vars
from ..inventory import (
    find_ancestors,
    flatten_for_checklist,
    host_exists_anywhere,
    load_group_tree,
    write_host_to_inventory,
)
from ..models import Catalog, HostConfig
from ..ui.dialogs import Dialogs


def _summarize(entry: Any) -> str:
    """Return a short one-line summary for a catalog entry body.

    If ``entry`` is a mapping that carries an ``image`` key, surface it
    as ``"image: <value>"`` so container-catalog multiselects get a
    useful hint next to each tag. Anything else collapses to an empty
    string — ``dialog --checklist`` is happy with an empty item text.
    """
    if isinstance(entry, dict) and "image" in entry:
        return f"image: {entry['image']}"
    return ""


def _format_summary(host: HostConfig, catalogs: list[Catalog]) -> str:
    """Render the confirm-screen summary for a :class:`HostConfig`.

    Shows hostname / ansible_host / chosen leaves / computed ancestors /
    per-catalog picks, one section per line. Catalogs with zero picks
    are still listed (as ``"(none)"``) so the operator can see which
    catalogs they walked past before confirming.
    """
    lines: list[str] = []
    lines.append(f"Hostname:     {host.hostname}")
    lines.append(f"ansible_host: {host.ansible_host}")
    leaves = ", ".join(host.selected_leaf_groups) or "(none)"
    lines.append(f"Leaf groups:  {leaves}")
    ancestors = ", ".join(host.selected_ancestor_groups) or "(none)"
    lines.append(f"Ancestors:    {ancestors}")
    if catalogs:
        lines.append("")
        lines.append("Catalog selections:")
        for c in catalogs:
            picks = host.selected_catalog_entries.get(c.enabled_key, [])
            rendered = ", ".join(picks) if picks else "(none)"
            lines.append(f"  {c.name}: {rendered}")
    return "\n".join(lines)


def create_device(context: InventoryContext, ui: Dialogs) -> None:
    """Orchestrate the create-device flow end-to-end.

    See design §9.5 for the state machine. Cancel / Esc on hostname,
    group, ansible_host, or any catalog checklist aborts the flow and
    returns to the main menu without writing anything. Answering "No"
    on the confirm screen returns to the catalog step with hostname,
    ansible_host, and group selection preserved (R11.2).

    Writes are paired with rollback (R10.1, R10.2): the host_vars file
    is written first, then the ``hosts.yml`` entry. Any exception from
    the inventory write deletes the just-written host_vars file and
    surfaces the error via ``msgbox``.
    """
    tree = load_group_tree(context.hosts_file)

    # Hostname loop (R4.1–R4.5).
    while True:
        hostname = ui.inputbox("New device", "Ansible hostname:")
        if hostname is None:
            return
        hostname = hostname.strip()
        if not hostname:
            ui.msgbox("Error", "Hostname cannot be empty")
            continue
        if host_exists_anywhere(tree, hostname):
            ui.msgbox("Error", f"'{hostname}' already exists")
            continue
        break

    # Group selection (R5.1–R5.4, R5.8). Build the checklist once —
    # only selectable (leaf) rows reach the widget, non-leaf rows are
    # kept out entirely since dialog can't render "informational only".
    rows = flatten_for_checklist(tree)
    leaf_rows = [
        (tag, text, False) for tag, text, selectable in rows if selectable
    ]
    leafs = ui.checklist("Groups", leaf_rows)
    # R5.8: Cancel/Esc OR empty selection both abort back to menu.
    if leafs is None or not leafs:
        return

    # Compute ancestors with dedup-keep-order across all chosen leaves.
    ancestors: list[str] = []
    for leaf in leafs:
        for a in find_ancestors(tree, leaf):
            if a not in ancestors:
                ancestors.append(a)

    # ansible_host prompt (R6.1–R6.4). R6.3: store the input with
    # exactly one leading+trailing whitespace strip, nothing more.
    while True:
        raw = ui.inputbox("Connection", "IP or FQDN:")
        if raw is None:
            return
        ansible_host = raw.strip()
        if not ansible_host:
            ui.msgbox("Error", "ansible_host cannot be empty")
            continue
        break

    # Catalog loop. "No" on confirm returns here with prior state
    # preserved (R11.2); Cancel/Esc inside a per-catalog checklist
    # aborts the whole flow (R8.6).
    while True:
        catalogs = discover_catalogs(context, list(leafs) + ancestors)
        selections: dict[str, list[str]] = {}
        aborted = False
        for c in catalogs:
            cat_rows = [
                (name, _summarize(entry), False)
                for name, entry in c.entries.items()
            ]
            picked = ui.checklist(c.name, cat_rows)
            if picked is None:
                aborted = True
                break
            selections[c.enabled_key] = picked
        if aborted:
            return

        host = HostConfig(
            hostname=hostname,
            ansible_host=ansible_host,
            selected_leaf_groups=list(leafs),
            selected_ancestor_groups=list(ancestors),
            selected_catalog_entries=selections,
        )

        if ui.yesno("Confirm", _format_summary(host, catalogs)):
            break
        # else: loop back to catalog step with prior state preserved.

    # Paired write with rollback (R9.4, R9.5, R10.1–R10.4, R12.3).
    hv_path = context.host_vars_dir / (hostname + ".yml")
    if hv_path.exists():
        if not ui.yesno(
            "Overwrite?",
            f"host_vars file already exists:\n{hv_path}\n\nOverwrite?",
        ):
            return
        overwrite = True
    else:
        overwrite = False

    try:
        written_hv = write_host_vars(context, host, catalogs, overwrite=overwrite)
    except (PermissionError, OSError) as exc:
        ui.msgbox("Error", f"failed to write host_vars: {exc}")
        return

    try:
        write_host_to_inventory(context.hosts_file, hostname, list(leafs))
    except Exception as exc:  # noqa: BLE001 - intentional catch-all for rollback
        written_hv.unlink(missing_ok=True)
        ui.msgbox(
            "Error",
            f"failed to write hosts.yml: {exc}\nRolled back host_vars.",
        )
        return

    ui.msgbox("Done", f"Created {hostname}")
