"""CLI entry point for `python -m frankinception`.

Delegates to :func:`frankinception.app.run`, which wires dependency checking,
context selection, and the main menu loop. Kept intentionally tiny so that the
module can be invoked as `python -m frankinception` on a target host.
"""

import sys

from .app import run


def main() -> int:
    """Run the frankinception TUI and return its exit status."""
    return run()


if __name__ == "__main__":
    sys.exit(main())
