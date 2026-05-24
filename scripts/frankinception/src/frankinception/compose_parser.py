"""Parse docker-compose services and ``docker run`` commands into catalog entries.

The output schema matches ``docker_containers_catalog`` in
``group_vars/docker/docker_catalog.yml`` — keys are container names, values
hold ``image``, ``ports``, ``volumes``, ``env``, ``networks``, ``devices``,
``restart_policy``, ``command``, ``labels``.

Volume host paths are *not* rewritten here. The caller is responsible for
mapping them against ``docker_bind_catalog`` (see ``bind_mapper.py``) so the
TUI can prompt the user when no match exists.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


@dataclass
class ParsedContainer:
    """A single container ready for catalog mapping."""

    name: str
    image: str = ""
    ports: list[str] = field(default_factory=list)
    volumes: list[str] = field(default_factory=list)
    """Raw ``host:container[:mode]`` strings, before bind-catalog rewriting."""
    env: dict[str, str] = field(default_factory=dict)
    networks: list[str] = field(default_factory=list)
    devices: list[str] = field(default_factory=list)
    restart_policy: str | None = None
    command: str | list[str] | None = None
    labels: dict[str, str] = field(default_factory=dict)

    def as_catalog_entry(self) -> dict[str, Any]:
        """Drop empty fields so the catalog stays readable."""
        body: dict[str, Any] = {"image": self.image}
        if self.ports:
            body["ports"] = list(self.ports)
        if self.volumes:
            body["volumes"] = list(self.volumes)
        if self.env:
            body["env"] = dict(self.env)
        if self.networks:
            body["networks"] = list(self.networks)
        if self.devices:
            body["devices"] = list(self.devices)
        if self.restart_policy:
            body["restart_policy"] = self.restart_policy
        if self.command is not None:
            body["command"] = self.command
        if self.labels:
            body["labels"] = dict(self.labels)
        return body


# ---- compose -----------------------------------------------------------------


def parse_compose(text: str) -> list[ParsedContainer]:
    """Parse a docker-compose YAML document.

    Only the fields we care about are extracted. Compose extension features
    like ``extends:``, ``depends_on:``, healthchecks, build contexts, and
    ``profiles:`` are intentionally ignored — they don't map to the catalog.
    """
    yaml = YAML(typ="safe")
    data = yaml.load(text) or {}
    services = data.get("services") or {}
    out: list[ParsedContainer] = []
    if not isinstance(services, dict):
        return out
    for name, body in services.items():
        if not isinstance(body, dict):
            continue
        out.append(_compose_service_to_container(str(name), body))
    return out


def _compose_service_to_container(name: str, body: dict[str, Any]) -> ParsedContainer:
    container = ParsedContainer(
        name=str(body.get("container_name") or name),
        image=str(body.get("image") or ""),
    )

    # Ports may be ["8080:80"] or [{"published": 8080, "target": 80}]
    for entry in _as_list(body.get("ports")):
        rendered = _compose_port(entry)
        if rendered:
            container.ports.append(rendered)

    # Volumes: short ("h:c[:mode]") or long form mappings.
    for entry in _as_list(body.get("volumes")):
        rendered = _compose_volume(entry)
        if rendered:
            container.volumes.append(rendered)

    # environment: list ["KEY=value"] or mapping {KEY: value}.
    env = body.get("environment")
    if isinstance(env, dict):
        container.env = {str(k): _stringify(v) for k, v in env.items()}
    elif isinstance(env, list):
        for item in env:
            if not isinstance(item, str) or "=" not in item:
                continue
            k, v = item.split("=", 1)
            container.env[k] = v

    # networks: list of names or mapping of name -> options.
    nets = body.get("networks")
    if isinstance(nets, dict):
        container.networks = [str(k) for k in nets.keys()]
    elif isinstance(nets, list):
        container.networks = [str(n) for n in nets if isinstance(n, (str, int))]

    for entry in _as_list(body.get("devices")):
        if isinstance(entry, str):
            container.devices.append(entry)

    if body.get("restart"):
        container.restart_policy = str(body["restart"])

    if "command" in body:
        container.command = body["command"]

    labels = body.get("labels")
    if isinstance(labels, dict):
        container.labels = {str(k): _stringify(v) for k, v in labels.items()}
    elif isinstance(labels, list):
        for item in labels:
            if not isinstance(item, str) or "=" not in item:
                continue
            k, v = item.split("=", 1)
            container.labels[k] = v

    return container


def _compose_port(entry: Any) -> str | None:
    if isinstance(entry, (str, int)):
        return str(entry)
    if isinstance(entry, dict):
        published = entry.get("published")
        target = entry.get("target")
        proto = entry.get("protocol")
        if published is None and target is None:
            return None
        rendered = f"{published}:{target}" if published is not None else str(target)
        if proto:
            rendered = f"{rendered}/{proto}"
        return rendered
    return None


def _compose_volume(entry: Any) -> str | None:
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        # Long-form: {type, source, target, read_only}
        src = entry.get("source")
        tgt = entry.get("target")
        if src is None or tgt is None:
            return None
        rendered = f"{src}:{tgt}"
        if entry.get("read_only"):
            rendered += ":ro"
        return rendered
    return None


# ---- docker run --------------------------------------------------------------


def parse_docker_run(line: str) -> ParsedContainer:
    """Parse a single ``docker run …`` command into a container.

    Handles the common flags people actually use: ``-p``, ``-v``,
    ``--mount``, ``-e``, ``--env-file``, ``--name``, ``--network``,
    ``--device``, ``--restart``, ``--label``. Unknown flags are skipped
    rather than raising — best-effort parsing.
    """
    tokens = shlex.split(line)
    if not tokens:
        raise ValueError("empty docker run command")
    if tokens[0] == "docker":
        tokens.pop(0)
    if tokens and tokens[0] == "container":
        tokens.pop(0)
    if tokens and tokens[0] == "run":
        tokens.pop(0)

    container = ParsedContainer(name="")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in {"-d", "--detach", "--rm", "-it", "-i", "-t", "--init"}:
            i += 1
            continue
        # --flag=value or --flag value
        flag, value, consumed = _split_flag(tokens, i)
        if flag is None:
            break  # positional → image follows
        i += consumed

        if flag in {"--name"}:
            container.name = value or ""
        elif flag in {"-p", "--publish"}:
            if value:
                container.ports.append(value)
        elif flag in {"-v", "--volume"}:
            if value:
                container.volumes.append(value)
        elif flag == "--mount":
            rendered = _mount_to_volume(value or "")
            if rendered:
                container.volumes.append(rendered)
        elif flag in {"-e", "--env"}:
            if value and "=" in value:
                k, v = value.split("=", 1)
                container.env[k] = v
        elif flag == "--network":
            if value:
                container.networks.append(value)
        elif flag == "--device":
            if value:
                container.devices.append(value)
        elif flag == "--restart":
            container.restart_policy = value
        elif flag == "--label":
            if value and "=" in value:
                k, v = value.split("=", 1)
                container.labels[k] = v
        # Anything else is silently ignored.

    # Remaining tokens are: <image> [command...]
    remaining = tokens[i:]
    if remaining:
        container.image = remaining[0]
        if len(remaining) > 1:
            container.command = remaining[1:]

    if not container.name:
        # Derive a name from the image if --name wasn't given.
        container.name = _derive_name(container.image)

    return container


def _split_flag(tokens: list[str], i: int) -> tuple[str | None, str | None, int]:
    """Return (flag, value, tokens consumed) starting at ``i``.

    Returns (None, None, 0) if the current token isn't a flag (i.e. it's a
    positional arg like the image name).
    """
    tok = tokens[i]
    if not tok.startswith("-"):
        return None, None, 0
    if "=" in tok:
        flag, _, value = tok.partition("=")
        return flag, value, 1
    # Boolean flags should have been handled before reaching here. Anything
    # else is assumed to take a value.
    if i + 1 >= len(tokens):
        return tok, None, 1
    return tok, tokens[i + 1], 2


def _mount_to_volume(spec: str) -> str | None:
    """Convert ``--mount type=bind,source=/x,target=/y[,readonly]`` to short form."""
    parts = dict(p.split("=", 1) for p in spec.split(",") if "=" in p)
    src = parts.get("source") or parts.get("src")
    tgt = parts.get("target") or parts.get("destination") or parts.get("dst")
    if not src or not tgt:
        return None
    rendered = f"{src}:{tgt}"
    if parts.get("readonly") in {"true", ""} or "readonly" in spec.split(","):
        rendered += ":ro"
    return rendered


def _derive_name(image: str) -> str:
    if not image:
        return "container"
    # registry/owner/name:tag → name
    name = image.split("/")[-1].split(":")[0]
    return name or "container"


# ---- helpers -----------------------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _stringify(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# ---- format detection --------------------------------------------------------


def parse_any(text: str) -> list[ParsedContainer]:
    """Detect compose vs docker-run heuristically and parse accordingly.

    Compose detection: contains a top-level ``services:`` key. Otherwise we
    treat each non-empty line beginning with ``docker`` (or any line if there
    is exactly one) as a docker-run command.
    """
    stripped = text.strip()
    if not stripped:
        return []
    if "services:" in stripped and stripped.lstrip().startswith(("services:", "version:", "name:")):
        return parse_compose(stripped)
    if stripped.lstrip().startswith("services:"):
        return parse_compose(stripped)
    # docker run lines
    out: list[ParsedContainer] = []
    for line in stripped.splitlines():
        line = line.strip().rstrip("\\").strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("docker") or "run" in line.split():
            out.append(parse_docker_run(line))
    if not out:
        # Fallback: treat the whole blob as one command.
        out.append(parse_docker_run(" ".join(stripped.split())))
    return out


def parse_file(path: Path) -> list[ParsedContainer]:
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".yml", ".yaml"}:
        return parse_compose(text)
    return parse_any(text)
