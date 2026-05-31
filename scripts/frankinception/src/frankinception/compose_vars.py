"""Detect and resolve compose-style ``${VAR}`` references in parsed containers.

Compose supports a handful of substitution forms:

* ``${VAR}`` — required, no default
* ``${VAR:-default}`` — empty/unset default (recommended form)
* ``${VAR-default}`` — unset default
* ``${VAR:?error}`` — required, error if empty/unset
* ``${VAR?error}`` — required, error if unset

We treat ``-`` / ``:-`` operators as supplying a default, and ``?`` / ``:?``
as supplying an error message (no default available).

Bare ``$VAR`` (no braces) is intentionally not detected — it's ambiguous
in shell-like values and rare in modern compose files. Users with bare
references can edit them manually after import.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator


VAR_PATTERN = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"(?:(?P<op>:?-|:?\?)(?P<arg>[^}]*))?"
    r"\}"
)


@dataclass(frozen=True)
class VarUsage:
    """One occurrence of ``${VAR}`` somewhere in a container."""

    container: str
    field_path: tuple[str, ...]
    """Path through the container struct, e.g. ``("env", "POSTGRES_PASSWORD")``."""
    raw: str
    """The literal text to find-and-replace, e.g. ``${POSTGRES_PASSWORD}``."""


@dataclass
class VarToResolve:
    """A variable name needing resolution, deduplicated across containers.

    The same ``${VAR}`` referenced in multiple places is shown once in the
    UI and resolved once — the resolution applies everywhere the variable
    appears, matching compose's own substitution semantics.
    """

    name: str
    usages: list[VarUsage] = field(default_factory=list)
    default: str | None = None
    """First non-None default seen across all usages."""

    resolved: str | None = None
    """User-provided substitution. Empty string and None both count as
    'unresolved' for proceed-checks, but None means 'never touched' for UI
    display purposes."""

    @property
    def raw_forms(self) -> set[str]:
        """All ``${...}`` literal forms this variable appears as."""
        return {u.raw for u in self.usages}

    @property
    def has_default(self) -> bool:
        return self.default is not None

    @property
    def has_resolution(self) -> bool:
        return self.resolved is not None and self.resolved != ""

    @property
    def can_proceed(self) -> bool:
        """True if this variable can be applied without further input.

        We accept either an explicit user resolution or a default — if
        ``${VAR:-foo}`` is left untouched, the default ``foo`` will be
        substituted at apply time.
        """
        return self.has_resolution or self.has_default

    @property
    def effective_value(self) -> str | None:
        """The value that will be substituted when apply_resolutions runs."""
        if self.has_resolution:
            return self.resolved
        return self.default

    @property
    def usage_summary(self) -> str:
        """Human-readable list of where the var is used."""
        seen: list[str] = []
        for u in self.usages:
            label = f"{u.container}.{'.'.join(u.field_path)}"
            if label not in seen:
                seen.append(label)
        return ", ".join(seen)


# ---- detection ----------------------------------------------------------


def find_variables(containers) -> list[VarToResolve]:  # noqa: ANN001
    """Walk all containers and group every ``${VAR}`` occurrence by name.

    Returned list is sorted by name for stable UI ordering.
    """
    by_name: dict[str, VarToResolve] = {}
    for container in containers:
        for path, value in _walk_string_fields(container):
            for match in VAR_PATTERN.finditer(value):
                name = match.group("name")
                op = match.group("op")
                arg = match.group("arg")
                # Only ``-`` / ``:-`` carry a default — ``?`` / ``:?`` carry
                # an error message which we shouldn't pre-fill into the
                # resolution field.
                default = arg if op in ("-", ":-") else None

                vt = by_name.get(name)
                if vt is None:
                    vt = VarToResolve(name=name, default=default)
                    by_name[name] = vt
                elif vt.default is None and default is not None:
                    vt.default = default

                vt.usages.append(
                    VarUsage(container=container.name, field_path=path, raw=match.group(0))
                )
    return sorted(by_name.values(), key=lambda v: v.name)


def _walk_string_fields(container) -> Iterator[tuple[tuple[str, ...], str]]:  # noqa: ANN001
    """Yield ``(field_path, string_value)`` pairs for every scannable field."""
    if isinstance(container.image, str):
        yield ("image",), container.image
    if isinstance(container.command, str):
        yield ("command",), container.command
    elif isinstance(container.command, list):
        for i, c in enumerate(container.command):
            if isinstance(c, str):
                yield ("command", str(i)), c
    for k, v in container.env.items():
        if isinstance(v, str):
            yield ("env", k), v
    for i, v in enumerate(container.volumes):
        if isinstance(v, str):
            yield ("volumes", str(i)), v
    for i, v in enumerate(container.ports):
        if isinstance(v, str):
            yield ("ports", str(i)), v
    for k, v in container.labels.items():
        if isinstance(v, str):
            yield ("labels", k), v
    for i, v in enumerate(container.devices):
        if isinstance(v, str):
            yield ("devices", str(i)), v
    for i, v in enumerate(container.networks):
        if isinstance(v, str):
            yield ("networks", str(i)), v


# ---- resolution application ---------------------------------------------


def apply_resolutions(containers, vars_to_resolve: Iterable[VarToResolve]) -> None:  # noqa: ANN001
    """Replace every ``${VAR}`` in every container with its resolved value.

    Mutates containers in place. Variables with no resolution and no
    default are left as-is so the user sees the literal ``${...}`` in the
    catalog and notices something went wrong.
    """
    for var in vars_to_resolve:
        replacement = var.effective_value
        if replacement is None:
            continue
        for raw in var.raw_forms:
            for container in containers:
                _replace_in_container(container, raw, replacement)


def _replace_in_container(container, old: str, new: str) -> None:  # noqa: ANN001
    if isinstance(container.image, str):
        container.image = container.image.replace(old, new)
    if isinstance(container.command, str):
        container.command = container.command.replace(old, new)
    elif isinstance(container.command, list):
        container.command = [
            c.replace(old, new) if isinstance(c, str) else c for c in container.command
        ]
    container.env = {k: v.replace(old, new) for k, v in container.env.items()}
    container.volumes = [v.replace(old, new) for v in container.volumes]
    container.ports = [p.replace(old, new) for p in container.ports]
    container.labels = {k: v.replace(old, new) for k, v in container.labels.items()}
    container.devices = [d.replace(old, new) for d in container.devices]
    container.networks = [n.replace(old, new) for n in container.networks]


# ---- env file discovery + parsing ---------------------------------------


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a compose-style ``.env`` file (``KEY=VALUE`` lines).

    Comments (``#``) and blank lines are ignored. Surrounding quotes on
    values are stripped. Returns ``{}`` for missing or unreadable files.
    """
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        out[key] = value
    return out


def collect_env_vars(
    compose_dir: Path | None,
    containers,  # noqa: ANN001
) -> dict[str, str]:
    """Combine vars from ``compose_dir/.env`` plus any ``env_file:`` paths.

    Later sources override earlier ones, matching docker compose's
    precedence rules. Any path that fails to load is silently skipped —
    we don't want a missing env file to abort the whole import.
    """
    out: dict[str, str] = {}
    if compose_dir is not None:
        out.update(parse_env_file(compose_dir / ".env"))
        # env_file paths are resolved relative to the compose file's dir.
        for container in containers:
            for raw in getattr(container, "env_file_paths", []) or []:
                p = Path(raw)
                if not p.is_absolute():
                    p = compose_dir / p
                out.update(parse_env_file(p))
    return out
