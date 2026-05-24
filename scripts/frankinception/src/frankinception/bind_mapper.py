"""Map raw container volume strings against ``docker_bind_catalog`` entries.

The bind catalog declares logical mount names (``media``, ``config``, ``nott``…)
each with a host ``src`` and an in-container ``mnt`` prefix. When importing a
docker run / compose volume like ``/Caleb/docker/configs/jellyfin:/config``,
we want to produce ``'{{ docker_bind_catalog.config.mnt }}/jellyfin:/config'``
so the catalog stays portable across hosts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class VolumeMatch:
    """Result of looking up a volume's host path in the bind catalog."""

    rendered: str
    """The volume string ready to write to the catalog."""

    bind_key: str | None
    """Which ``docker_bind_catalog`` entry was matched, if any."""

    raw_host_path: str
    """Original host path (before rewriting)."""

    raw_container_path: str
    """Container-side mount path."""

    mode: str | None
    """``ro``/``rw`` if specified."""

    needs_user_choice: bool
    """True when no bind matched and the UI should prompt."""


def split_volume(volume: str) -> tuple[str, str, str | None]:
    """Split ``host:container[:mode]`` into its three parts.

    Returns ``(host, container, mode)``. Raises ValueError if the input is
    not a valid bind-style volume.
    """
    parts = volume.split(":")
    if len(parts) == 2:
        host, container = parts
        return host, container, None
    if len(parts) == 3:
        host, container, mode = parts
        return host, container, mode
    raise ValueError(f"unrecognised volume spec: {volume!r}")


def match_volume(volume: str, bind_catalog: dict[str, Any]) -> VolumeMatch:
    """Try to rewrite ``volume`` using the longest matching bind ``src``.

    A volume string that is not a host:container mapping (e.g. a named
    docker volume like ``mydata:/data``) is returned unchanged with
    ``needs_user_choice=False``.
    """
    try:
        host, container, mode = split_volume(volume)
    except ValueError:
        return VolumeMatch(
            rendered=volume,
            bind_key=None,
            raw_host_path=volume,
            raw_container_path="",
            mode=None,
            needs_user_choice=False,
        )

    # Named docker volume (no leading slash, no trailing path) — leave alone.
    if not host.startswith("/") and "/" not in host:
        return VolumeMatch(
            rendered=volume,
            bind_key=None,
            raw_host_path=host,
            raw_container_path=container,
            mode=mode,
            needs_user_choice=False,
        )

    best_key: str | None = None
    best_src: str = ""
    for key, entry in bind_catalog.items():
        if not isinstance(entry, dict):
            continue
        src = entry.get("src")
        if not isinstance(src, str):
            continue
        # Match either the exact path or a path prefix at a directory boundary.
        normalised = src.rstrip("/")
        if host == normalised or host.startswith(normalised + "/"):
            if len(normalised) > len(best_src):
                best_key = key
                best_src = normalised

    if best_key is None:
        return VolumeMatch(
            rendered=volume,
            bind_key=None,
            raw_host_path=host,
            raw_container_path=container,
            mode=mode,
            needs_user_choice=True,
        )

    suffix = host[len(best_src):]
    rewritten_host = "{{ docker_bind_catalog." + best_key + ".mnt }}" + suffix
    rendered = f"{rewritten_host}:{container}"
    if mode:
        rendered += f":{mode}"
    return VolumeMatch(
        rendered=rendered,
        bind_key=best_key,
        raw_host_path=host,
        raw_container_path=container,
        mode=mode,
        needs_user_choice=False,
    )


def add_bind(
    bind_catalog: dict[str, Any], key: str, src: str, mnt: str
) -> None:
    """Insert a new bind entry into the catalog (in-place)."""
    bind_catalog[key] = {"src": src, "mnt": mnt}
