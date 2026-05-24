"""Round-trip YAML I/O.

We use ruamel.yaml in round-trip mode so comments, key order, and quoting
survive edits. Every file we write was loaded with the same instance, so
formatting drift is minimal.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq


def make_yaml() -> YAML:
    """Build a YAML round-tripper configured for our inventory files."""
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 120
    return yaml


_YAML = make_yaml()


def load(path: Path) -> Any:
    """Load a YAML file; return None for empty files."""
    if not path.exists() or path.stat().st_size == 0:
        return None
    with path.open("r", encoding="utf-8") as fh:
        return _YAML.load(fh)


def dump(data: Any, path: Path) -> None:
    """Write data to ``path``, creating parent dirs as needed.

    A trailing newline is always present.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = StringIO()
    _YAML.dump(data, buf)
    text = buf.getvalue()
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")


def to_str(data: Any) -> str:
    """Render data as a YAML string (used for diff previews)."""
    buf = StringIO()
    _YAML.dump(data, buf)
    return buf.getvalue()


def empty_map() -> CommentedMap:
    return CommentedMap()


def empty_seq() -> CommentedSeq:
    return CommentedSeq()
