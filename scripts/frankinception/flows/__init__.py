"""Flow modules for frankinception.

Each flow orchestrates one top-level menu action end-to-end, composing the
inventory, catalog, host_vars, and UI layers. Only ``create_device`` is a
real flow in this iteration; the rest live in :mod:`frankinception.flows.stubs`.
"""
