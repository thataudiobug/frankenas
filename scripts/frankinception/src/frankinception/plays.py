"""Discover playbooks under ``plays/`` and extract a description for each.

Description resolution:

1. If the file's first non-blank line is a top-level comment (``# ...``),
   strip the leading hash and use the comment text. We allow up to two
   consecutive comment lines before the YAML starts and join them with " — ".
2. Otherwise, take each play's ``name:`` field and join them with "; ".
3. Fall back to the filename stem.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML


@dataclass(frozen=True)
class Playbook:
    name: str
    """The display name (file stem)."""

    path: Path
    """Absolute path to the playbook."""

    description: str
    """One-line description for the menu."""


def _read_top_comments(text: str) -> list[str]:
    """Collect leading ``#``-comments before any YAML content.

    A blank line at the very top is allowed. ``---`` document markers are
    treated as YAML, not part of the description.
    """
    out: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            if out:
                # Blank line ends the leading comment block.
                break
            continue
        if stripped.startswith("---"):
            break
        if stripped.startswith("#"):
            out.append(stripped.lstrip("#").strip())
            continue
        break
    return out


def _play_names(text: str) -> list[str]:
    yaml = YAML(typ="safe")
    try:
        data = yaml.load(text)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    names: list[str] = []
    for play in data:
        if isinstance(play, dict) and isinstance(play.get("name"), str):
            names.append(play["name"])
    return names


def describe(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return path.stem

    comments = _read_top_comments(text)
    if comments:
        return " — ".join(comments)

    names = _play_names(text)
    if names:
        return "; ".join(names)

    return path.stem


def list_playbooks(plays_dir: Path) -> list[Playbook]:
    if not plays_dir.is_dir():
        return []
    out: list[Playbook] = []
    for path in sorted(plays_dir.iterdir()):
        if not path.is_file():
            continue
        if path.suffix not in {".yml", ".yaml"}:
            continue
        # Skip docker-compose-style files that aren't playbooks.
        # docker_fleet_deploy.yml etc. all start with "- name:" or "---".
        # The cheap heuristic: if YAML loads as a dict (compose-style), skip.
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if _looks_like_compose(text):
            continue
        out.append(Playbook(name=path.stem, path=path, description=describe(path)))
    return out


def _looks_like_compose(text: str) -> bool:
    # Quick check before invoking the YAML parser.
    head = "\n".join(line for line in text.splitlines()[:20] if not line.lstrip().startswith("#"))
    return ("services:" in head and "version:" in head) or head.lstrip().startswith("services:")
