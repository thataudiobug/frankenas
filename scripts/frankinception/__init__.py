"""frankinception — terminal-dialog utility for managing the frankenas Ansible inventory.

This package provides an interactive TUI (built on python-dialog) for reading and
writing the `frankenas/inventories/{prod,test}/` layout: discovering catalogs,
adding new hosts to `hosts.yml`, and generating per-host `host_vars/` files while
preserving YAML round-trip formatting and comments.

See `.kiro/specs/frankinception/design.md` for the full design.
"""
