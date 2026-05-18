"""UI layer for frankinception.

Isolates every interaction with the `dialog` binary behind a thin wrapper so
the rest of the codebase can stay unaware of python-dialog specifics and so
non-interactive tests can stub the UI cleanly.
"""
