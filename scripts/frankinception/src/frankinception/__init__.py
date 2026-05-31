"""TUI for managing the frankenas Ansible inventory."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    # Single source of truth: ``pyproject.toml``. The metadata is populated
    # at install time, so ``pipx install --force`` always reflects what's in
    # the source tree without us needing to bump the version in two places.
    __version__ = _pkg_version("frankinception")
except PackageNotFoundError:
    # Falls back when running from a checkout that hasn't been installed
    # (e.g. ``python -m frankinception`` from the source tree). Keep this
    # in step with pyproject.toml on bumps.
    __version__ = "0.7.2"
