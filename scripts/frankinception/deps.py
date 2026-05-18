"""Dependency checker and installer.

On startup, verifies that the system `dialog` binary and required Python
packages (`python-dialog`, `ruamel.yaml`, …) are present. Offers to install
anything missing via apt / pipx / venv, respecting Ubuntu 24.04's PEP 668
externally-managed environment.
"""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys

# Required system (apt) packages — key is the binary we probe on PATH.
_REQUIRED_SYSTEM_BINARIES: tuple[str, ...] = ("dialog",)

# Required Python modules — importable names, not the distribution name.
# `dialog` is the import name of `pythondialog`.
_REQUIRED_PYTHON_MODULES: tuple[str, ...] = ("dialog", "ruamel.yaml")

# apt package names for missing Python modules (when pip is unavailable /
# declined). Not currently consulted by the install path, kept here for docs.
_PYTHON_APT_FALLBACK: dict[str, str] = {
    "dialog": "python3-pythondialog",
    "ruamel.yaml": "python3-ruamel.yaml",
}

# Idempotence flag (Property P8, Requirement R1.10). Once a run of
# `ensure_dependencies` has verified everything is present, subsequent calls
# return immediately without any side effects.
_VERIFIED: bool = False


def _running_in_venv() -> bool:
    """True iff the current interpreter is running inside a virtual env."""
    return sys.prefix != sys.base_prefix or hasattr(sys, "real_prefix")


def _check_system_deps() -> list[str]:
    """Return the list of missing required system binaries."""
    return [b for b in _REQUIRED_SYSTEM_BINARIES if shutil.which(b) is None]


def _check_python_deps() -> list[str]:
    """Return the list of missing required Python modules."""
    missing: list[str] = []
    for mod in _REQUIRED_PYTHON_MODULES:
        try:
            spec = importlib.util.find_spec(mod)
        except (ImportError, ValueError):
            spec = None
        if spec is None:
            missing.append(mod)
    return missing


def _print_summary(missing_system: list[str], missing_python: list[str]) -> None:
    """Plain-text summary printed before we have (or may have) `dialog`."""
    print("frankinception: missing required dependencies")
    if missing_system:
        print("  system packages (apt):")
        for pkg in missing_system:
            print(f"    - {pkg}")
    if missing_python:
        print("  Python packages:")
        for pkg in missing_python:
            print(f"    - {pkg}")


def _confirm(prompt: str) -> bool:
    """Read a yes/no answer from stdin. Empty / anything-not-y means No."""
    try:
        answer = input(prompt).strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def _install_system(packages: list[str]) -> None:
    """Install apt packages non-interactively."""
    subprocess.run(["sudo", "apt-get", "update"], check=False)
    subprocess.run(
        ["sudo", "apt-get", "install", "-y", *packages],
        check=False,
    )


def _install_python(packages: list[str]) -> None:
    """Install Python packages, picking the least-surprising strategy.

    Strategy order (per design §5.2):
      1. In a venv  -> `pip install ...` into that venv.
      2. `pipx`    -> pipx can't install individual library deps, so warn
                      the user and fall through to (3) with explicit consent.
      3. Otherwise -> warn about PEP 668 and, only on explicit confirmation,
                      `pip install --break-system-packages ...`.
    """
    if _running_in_venv():
        subprocess.run(
            [sys.executable, "-m", "pip", "install", *packages],
            check=False,
        )
        return

    if shutil.which("pipx") is not None:
        print(
            "Note: pipx installs application CLIs, not individual library "
            "packages. Frankinception's Python dependencies still need pip."
        )
        # Fall through to the --break-system-packages path with consent.

    print(
        "Warning: this interpreter is externally managed (PEP 668). "
        "The recommended fix is to run frankinception from a dedicated "
        "venv (python3 -m venv ~/.venvs/frankinception) or via pipx."
    )
    if not _confirm(
        "Proceed with `pip install --break-system-packages` anyway? [y/N] "
    ):
        print("Aborting: install declined.", file=sys.stderr)
        sys.exit(1)

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--break-system-packages",
            *packages,
        ],
        check=False,
    )


def ensure_dependencies() -> None:
    """Verify system + Python deps, offering to install anything missing.

    Idempotent: after a successful verification the module-level `_VERIFIED`
    flag is set to True and subsequent calls return immediately with no side
    effects (no prompts, no subprocess calls). This is Property P8 /
    Requirement R1.10.

    Exits the process with status 1 if the operator declines the install or
    if dependencies are still missing after an install attempt.
    """
    global _VERIFIED
    if _VERIFIED:
        return

    missing_system = _check_system_deps()
    missing_python = _check_python_deps()

    if not missing_system and not missing_python:
        _VERIFIED = True
        return

    # Pre-dialog UI: we can't use `dialog` here because `dialog` itself may
    # be what's missing. Fall back to plain stdin/stdout.
    _print_summary(missing_system, missing_python)
    if not _confirm("Install missing dependencies now? [y/N] "):
        print(
            "Cannot continue without required dependencies.",
            file=sys.stderr,
        )
        sys.exit(1)

    if missing_system:
        _install_system(missing_system)

    if missing_python:
        _install_python(missing_python)

    # Re-verify after install; fail loudly if anything is still missing.
    still_missing_system = _check_system_deps()
    still_missing_python = _check_python_deps()
    if still_missing_system or still_missing_python:
        print(
            "Dependency install did not resolve everything. Still missing: "
            f"system={still_missing_system} python={still_missing_python}",
            file=sys.stderr,
        )
        sys.exit(1)

    _VERIFIED = True
