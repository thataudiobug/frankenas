"""Smoke tests covering the non-TUI logic.

Specifically:
* ``paths.discover`` follows ``ansible.cfg``.
* ``Inventory`` round-trips the prod hosts.yml and resolves transitive groups.
* ``catalogs`` discovers the *_catalog files for a host's groups.
* ``compose_parser`` understands compose, mappings and lists, and docker run.
* ``bind_mapper`` rewrites volumes against ``docker_bind_catalog``.
* ``plays.list_playbooks`` extracts descriptions in the documented order.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from frankinception import paths, yaml_io
from frankinception.bind_mapper import match_volume
from frankinception.catalogs import CatalogKind, load_catalogs_for_groups
from frankinception.compose_parser import parse_any, parse_compose, parse_docker_run
from frankinception.hostvars import HostVars
from frankinception.inventory import Inventory
from frankinception.plays import describe, list_playbooks


REPO_ROOT = Path(__file__).resolve().parents[3]
FRANKENAS = REPO_ROOT / "frankenas"
INVENTORY = FRANKENAS / "inventories" / "prod"


def test_paths_discover_follows_ansible_cfg(monkeypatch, tmp_path):
    cfg = tmp_path / "ansible.cfg"
    cfg.write_text("[defaults]\ninventory = ./inv\n")
    (tmp_path / "inv").mkdir()
    monkeypatch.chdir(tmp_path)
    layout = paths.discover()
    assert layout.inventory_dir == (tmp_path / "inv").resolve()
    assert layout.ansible_cfg == cfg


def test_inventory_groups_for_real_hosts():
    inv = Inventory.load(INVENTORY / "hosts.yml")
    hosts = inv.hosts()
    assert "port-tortuga" in hosts
    assert "test-port-01" in hosts
    # port-tortuga is in qemu and docker, transitively in droplets, hosts_all,
    # and datacenter.
    groups = inv.all_groups_of("port-tortuga")
    for expected in ("qemu", "docker", "droplets", "hosts_all", "datacenter"):
        assert expected in groups, f"{expected} missing from {groups}"


def test_catalog_discovery_for_docker_host():
    inv = Inventory.load(INVENTORY / "hosts.yml")
    # test-port-01 is in pct (so it sees hardware_catalog) and docker.
    groups = inv.all_groups_of("test-port-01")
    catalogs = load_catalogs_for_groups(INVENTORY / "group_vars", groups)
    names = {c.name for c in catalogs}
    # Sanity: a docker+pct host should see all the major catalogs.
    expected = {
        "docker_bind_catalog",
        "docker_containers_catalog",
        "docker_groups_catalog",
        "compute_catalog",
        "storage_catalog",
        "network_catalog",
        "users_catalog",
    }
    missing = expected - names
    assert not missing, f"missing catalogs: {missing}"
    # docker_groups_catalog should be classed as MAPPING.
    by_name = {c.name: c for c in catalogs}
    assert by_name["docker_groups_catalog"].kind is CatalogKind.MAPPING
    assert by_name["compute_catalog"].kind is CatalogKind.SCALAR
    assert by_name["users_catalog"].kind is CatalogKind.LIST


def test_inventory_roundtrip_preserves_layout(tmp_path):
    src = INVENTORY / "hosts.yml"
    dst = tmp_path / "hosts.yml"
    dst.write_bytes(src.read_bytes())
    inv = Inventory.load(dst)
    inv.save()
    # ruamel can renormalise minor whitespace, but the parsed structure must
    # match exactly.
    assert yaml_io.load(dst) == yaml_io.load(src)


def test_hostvars_set_mapping_preserves_overrides(tmp_path):
    p = tmp_path / "host.yml"
    p.write_text(
        dedent(
            """
            docker_groups_enabled:
              public:
                jellyfin:
                  state: stopped
              piracy:
            """
        ).lstrip()
    )
    hv = HostVars(path=p, raw=yaml_io.load(p))

    class FakeCat:
        enabled_var = "docker_groups_enabled"
        kind = CatalogKind.MAPPING

    hv.set_mapping(FakeCat(), ["public", "proxys"])
    assert "piracy" not in hv.raw["docker_groups_enabled"]
    assert "proxys" in hv.raw["docker_groups_enabled"]
    # The nested override for public.jellyfin must survive.
    public = hv.raw["docker_groups_enabled"]["public"]
    assert public["jellyfin"]["state"] == "stopped"


def test_compose_parser_basic():
    text = dedent(
        """
        services:
          web:
            image: nginx:1.27
            ports:
              - "8080:80"
            volumes:
              - /srv/html:/usr/share/nginx/html:ro
            environment:
              FOO: bar
              BAZ: qux
            restart: unless-stopped
        """
    )
    [c] = parse_compose(text)
    assert c.name == "web"
    assert c.image == "nginx:1.27"
    assert c.ports == ["8080:80"]
    assert c.volumes == ["/srv/html:/usr/share/nginx/html:ro"]
    assert c.env == {"FOO": "bar", "BAZ": "qux"}
    assert c.restart_policy == "unless-stopped"


def test_compose_long_form_volume_and_env_list():
    text = dedent(
        """
        services:
          api:
            image: example:latest
            environment:
              - DB=postgres
              - DEBUG=1
            volumes:
              - type: bind
                source: /etc/app
                target: /etc/app
                read_only: true
        """
    )
    [c] = parse_compose(text)
    assert c.env == {"DB": "postgres", "DEBUG": "1"}
    assert c.volumes == ["/etc/app:/etc/app:ro"]


def test_docker_run_parser():
    line = (
        "docker run -d --name jelly --restart unless-stopped "
        "-p 8096:8096 -v /Caleb/docker/configs/jellyfin:/config "
        "-v /essek/media:/media:ro -e PUID=1000 -e PGID=1000 "
        "--device /dev/dri:/dev/dri jellyfin/jellyfin:latest"
    )
    c = parse_docker_run(line)
    assert c.name == "jelly"
    assert c.image == "jellyfin/jellyfin:latest"
    assert c.ports == ["8096:8096"]
    assert c.volumes == [
        "/Caleb/docker/configs/jellyfin:/config",
        "/essek/media:/media:ro",
    ]
    assert c.env == {"PUID": "1000", "PGID": "1000"}
    assert c.devices == ["/dev/dri:/dev/dri"]
    assert c.restart_policy == "unless-stopped"


def test_parse_any_picks_compose_or_run():
    compose = "services:\n  a:\n    image: foo\n"
    [c] = parse_any(compose)
    assert c.name == "a"
    [c2] = parse_any("docker run --name bar foo:latest")
    assert c2.name == "bar"


def test_bind_mapper_rewrites_against_real_catalog():
    bind_cat = yaml_io.load(
        INVENTORY / "group_vars" / "all" / "mounts_catalog.yml"
    )["docker_bind_catalog"]
    m = match_volume("/Caleb/docker/configs/jellyfin/config:/config", bind_cat)
    assert m.bind_key == "config"
    assert m.rendered == (
        "{{ docker_bind_catalog.config.mnt }}/jellyfin/config:/config"
    )

    m_named = match_volume("mydata:/var/lib/data", bind_cat)
    assert m_named.bind_key is None
    assert not m_named.needs_user_choice
    assert m_named.rendered == "mydata:/var/lib/data"

    m_unmatched = match_volume("/opt/something/else:/data", bind_cat)
    assert m_unmatched.bind_key is None
    assert m_unmatched.needs_user_choice


def test_bind_mapper_picks_longer_prefix():
    bind_cat = {
        "caleb": {"src": "/Caleb", "mnt": "/mnt/caleb"},
        "config": {"src": "/Caleb/docker/configs", "mnt": "/mnt/config"},
    }
    m = match_volume("/Caleb/docker/configs/foo:/etc/foo", bind_cat)
    assert m.bind_key == "config"


def test_plays_describe_uses_first_comment(tmp_path):
    p = tmp_path / "play.yml"
    p.write_text("# Run apt updates fleet-wide\n# Plus a reboot if needed\n---\n- name: Update\n  hosts: all\n")
    assert describe(p) == "Run apt updates fleet-wide — Plus a reboot if needed"


def test_plays_describe_falls_back_to_play_names(tmp_path):
    p = tmp_path / "play.yml"
    p.write_text(
        "- name: Provision\n  hosts: all\n- name: Configure\n  hosts: docker\n"
    )
    assert describe(p) == "Provision; Configure"


def test_list_playbooks_in_real_repo():
    plays = list_playbooks(FRANKENAS / "plays")
    by_name = {p.name: p for p in plays}
    assert "docker_fleet_deploy" in by_name
    # Two anonymous plays in docker_fleet_deploy → "Provision; Configure".
    assert by_name["docker_fleet_deploy"].description == "Provision; Configure"

