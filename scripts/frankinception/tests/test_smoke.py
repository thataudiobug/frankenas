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




# ---- vault ---------------------------------------------------------------


def _have_ansible_vault() -> bool:
    import shutil

    return shutil.which("ansible-vault") is not None


vault_only = pytest.mark.skipif(
    not _have_ansible_vault(), reason="ansible-vault not installed"
)


@vault_only
def test_vault_round_trip(tmp_path):
    """Encrypt a YAML mapping, read it back, and confirm we get the same data."""
    from frankinception import vault as vault_mod

    pw_file = tmp_path / "pw"
    pw_file.write_text("hunter2\n")
    pw_file.chmod(0o600)
    cfg = vault_mod.VaultConfig(password_file=pw_file)

    target = tmp_path / "vault.yml"
    payload = {
        "vault_robot_pub": "ssh-rsa AAAA... robot@host",
        "vault_pve_token": "xyz123",
        "vault_multiline": "line1\nline2\nline3",
    }
    vault_mod.save_vault_yaml(target, payload, cfg)

    assert vault_mod.is_encrypted(target)
    head = target.read_bytes().splitlines()[0]
    assert head.startswith(b"$ANSIBLE_VAULT")

    loaded = vault_mod.load_vault_yaml(target, cfg)
    assert loaded == payload


@vault_only
def test_vault_in_memory_password(tmp_path):
    """A VaultConfig with only an in-memory password should also work — the
    helper writes a transient password file and cleans it up.
    """
    from frankinception import vault as vault_mod

    cfg = vault_mod.VaultConfig(password=" sekrit ")
    target = tmp_path / "vault.yml"
    vault_mod.save_vault_yaml(target, {"vault_a": "1"}, cfg)
    assert vault_mod.load_vault_yaml(target, cfg) == {"vault_a": "1"}


def test_load_vault_yaml_missing_returns_empty(tmp_path):
    from frankinception import vault as vault_mod

    cfg = vault_mod.VaultConfig(password_file=tmp_path / "ignored")
    missing = tmp_path / "does_not_exist.yml"
    assert vault_mod.load_vault_yaml(missing, cfg) == {}


def test_load_vault_yaml_rejects_non_mapping(tmp_path):
    """A vault that decrypts to a list (not a dict) should error cleanly so
    the user knows their file isn't shaped how the tool expects.
    """
    from frankinception import vault as vault_mod

    plain = tmp_path / "plain.yml"
    plain.write_text("- one\n- two\n")
    cfg = vault_mod.VaultConfig(password_file=tmp_path / "ignored")
    with pytest.raises(vault_mod.VaultError):
        vault_mod.load_vault_yaml(plain, cfg)



# ---- runner password handling -----------------------------------------------


def test_runner_creates_and_cleans_up_transient_password_file(tmp_path):
    """``build`` writes the password to a 0600 file; ``cleanup`` removes it."""
    from frankinception import runner

    play = tmp_path / "play.yml"
    play.write_text("- hosts: all\n  tasks: []\n")
    inv = runner.build(
        playbook=play,
        project_root=tmp_path,
        inventory_dir=None,
        vault_password="hunter2",
    )
    assert len(inv.sensitive_files) == 1
    pw_path = inv.sensitive_files[0]
    assert pw_path.exists()
    assert pw_path.read_text(encoding="utf-8").strip() == "hunter2"
    # Mode is 0600 (owner read/write only).
    mode = pw_path.stat().st_mode & 0o777
    assert mode == 0o600

    # The argv references the password file and exposes its path but not
    # its contents.
    argv = " ".join(inv.argv)
    assert "--vault-password-file" in argv
    assert str(pw_path) in argv
    assert "hunter2" not in argv

    runner.cleanup(inv)
    assert not pw_path.exists()


def test_runner_no_password_means_no_sensitive_files(tmp_path):
    """When no password is passed, no transient file should be created."""
    from frankinception import runner

    play = tmp_path / "play.yml"
    play.write_text("- hosts: all\n  tasks: []\n")
    inv = runner.build(playbook=play, project_root=tmp_path, inventory_dir=None)
    assert inv.sensitive_files == ()
    assert "--vault-password-file" not in " ".join(inv.argv)


def test_vault_password_file_context_manager_cleans_up():
    from frankinception import runner

    with runner.vault_password_file("xyz") as path:
        assert path.exists()
        assert path.read_text().strip() == "xyz"
    assert not path.exists()


# ---- new host flow ----------------------------------------------------------


def test_new_host_creation_writes_inventory_and_host_vars(tmp_path):
    """Simulate the inventory edits the new-host screen performs without
    spinning up the full Textual app: add a host to a few groups and verify
    the expected files exist.
    """
    from frankinception.hostvars import HostVars
    from frankinception.inventory import Inventory

    # Stand up a minimal inventory.
    hosts_yml = tmp_path / "hosts.yml"
    hosts_yml.write_text(
        "docker:\n"
        "  hosts: {}\n"
        "qemu:\n"
        "  hosts: {}\n"
    )
    inv = Inventory.load(hosts_yml)
    inv.add_host_to_group("port-essos", "docker")
    inv.add_host_to_group("port-essos", "qemu")
    inv.save()

    # Reload to confirm persistence.
    reloaded = Inventory.load(hosts_yml)
    assert "port-essos" in reloaded.hosts()
    assert set(reloaded.direct_groups_of("port-essos")) == {"docker", "qemu"}

    host_vars_dir = tmp_path / "host_vars"
    hv = HostVars.load(host_vars_dir, "port-essos")
    hv.set("ansible_host", "192.168.1.150")
    hv.save()

    saved = (host_vars_dir / "port-essos.yml").read_text()
    assert "ansible_host: 192.168.1.150" in saved



def test_runner_extra_args_are_appended_last(tmp_path):
    """Extra flags from the user should land at the very end of argv so
    they can override anything we put in front (e.g. a built-in --check).
    """
    from frankinception import runner

    play = tmp_path / "play.yml"
    play.write_text("- hosts: all\n  tasks: []\n")
    inv = runner.build(
        playbook=play,
        project_root=tmp_path,
        inventory_dir=tmp_path,
        limit="port-tortuga",
        check=True,
        extra_args=["-vvv", "--tags", "docker"],
    )
    # The trailing tail of argv must be the extras in order.
    assert inv.argv[-3:] == ["-vvv", "--tags", "docker"]
    # And the built-in flags must still be present in front.
    assert "--limit" in inv.argv
    assert "--check" in inv.argv


def test_extra_args_input_parses_quoted_values():
    """``shlex.split`` is what we use under the hood — confirm the obvious
    cases (single-token, multi-token, quoted multi-word) all work without
    leaking the quotes into the argv we'd hand to ansible-playbook.
    """
    import shlex

    assert shlex.split("-vvv") == ["-vvv"]
    assert shlex.split("-vvv --tags docker") == ["-vvv", "--tags", "docker"]
    assert shlex.split("--extra-vars 'foo=bar baz'") == [
        "--extra-vars",
        "foo=bar baz",
    ]



# ---- compose variable detection / resolution -------------------------------


def test_find_variables_extracts_names_and_defaults():
    from frankinception.compose_parser import parse_compose
    from frankinception.compose_vars import find_variables

    text = (
        "services:\n"
        "  immich:\n"
        "    image: ghcr.io/immich-app/immich-server:${IMMICH_VERSION:-release}\n"
        "    environment:\n"
        "      DB_PASSWORD: ${POSTGRES_PASSWORD}\n"
        "      DB_HOST: ${DB_HOST:-postgres}\n"
        "      UPLOAD: ${UPLOAD_LOCATION}\n"
        "    volumes:\n"
        "      - ${UPLOAD_LOCATION}:/data\n"
    )
    containers = parse_compose(text)
    vars_ = find_variables(containers)
    by_name = {v.name: v for v in vars_}
    assert set(by_name) == {
        "IMMICH_VERSION",
        "POSTGRES_PASSWORD",
        "DB_HOST",
        "UPLOAD_LOCATION",
    }
    # Defaults are picked up from `:-` and `-` forms.
    assert by_name["IMMICH_VERSION"].default == "release"
    assert by_name["DB_HOST"].default == "postgres"
    # No default present → has_default False.
    assert by_name["POSTGRES_PASSWORD"].default is None
    # UPLOAD_LOCATION shows up in two places — both usages should be tracked.
    assert len(by_name["UPLOAD_LOCATION"].usages) == 2


def test_find_variables_handles_required_form():
    """``${VAR:?msg}`` and ``${VAR?msg}`` are required-with-error syntax —
    we should detect the variable but not pull the message in as a default.
    """
    from frankinception.compose_parser import parse_compose
    from frankinception.compose_vars import find_variables

    text = (
        "services:\n"
        "  app:\n"
        "    image: ${IMG:?must be set}\n"
        "    environment:\n"
        "      KEY: ${SECRET?missing secret}\n"
    )
    containers = parse_compose(text)
    by_name = {v.name: v for v in find_variables(containers)}
    assert by_name["IMG"].default is None
    assert by_name["SECRET"].default is None


def test_apply_resolutions_substitutes_everywhere():
    from frankinception.compose_parser import parse_compose
    from frankinception.compose_vars import apply_resolutions, find_variables

    text = (
        "services:\n"
        "  app:\n"
        "    image: foo/bar:${TAG:-latest}\n"
        "    environment:\n"
        "      DB_URL: postgres://user:${DB_PW}@host/db\n"
        "    volumes:\n"
        "      - ${UPLOAD}:/data\n"
    )
    containers = parse_compose(text)
    vars_ = find_variables(containers)
    by_name = {v.name: v for v in vars_}
    by_name["TAG"].resolved = "v1.2.3"
    by_name["DB_PW"].resolved = "{{ vault_db_pw }}"
    by_name["UPLOAD"].resolved = "{{ docker_bind_catalog.media.mnt }}/uploads"

    apply_resolutions(containers, vars_)
    [c] = containers
    assert c.image == "foo/bar:v1.2.3"
    assert c.env["DB_URL"] == "postgres://user:{{ vault_db_pw }}@host/db"
    assert c.volumes == ["{{ docker_bind_catalog.media.mnt }}/uploads:/data"]


def test_apply_resolutions_falls_back_to_default():
    """A variable that has a compose default and no user resolution should
    still be substituted with its default.
    """
    from frankinception.compose_parser import parse_compose
    from frankinception.compose_vars import apply_resolutions, find_variables

    text = (
        "services:\n"
        "  app:\n"
        "    image: foo/bar:${TAG:-latest}\n"
    )
    containers = parse_compose(text)
    vars_ = find_variables(containers)
    apply_resolutions(containers, vars_)
    assert containers[0].image == "foo/bar:latest"


def test_apply_resolutions_leaves_unresolved_vars_in_place():
    """When neither a default nor a resolution exists, the literal stays
    in the output so the catalog visibly fails the round-trip.
    """
    from frankinception.compose_parser import parse_compose
    from frankinception.compose_vars import apply_resolutions, find_variables

    text = (
        "services:\n"
        "  app:\n"
        "    environment:\n"
        "      KEY: ${MUST_BE_SET}\n"
    )
    containers = parse_compose(text)
    apply_resolutions(containers, find_variables(containers))
    assert containers[0].env["KEY"] == "${MUST_BE_SET}"


def test_parse_env_file_handles_quotes_and_comments(tmp_path):
    from frankinception.compose_vars import parse_env_file

    env = tmp_path / ".env"
    env.write_text(
        "# comment line\n"
        "FOO=bar\n"
        "BAZ='quoted value'\n"
        'QUX="double quoted"\n'
        "EMPTY=\n"
        "\n"
        "WITH_EQUALS=a=b=c\n"
    )
    parsed = parse_env_file(env)
    assert parsed == {
        "FOO": "bar",
        "BAZ": "quoted value",
        "QUX": "double quoted",
        "EMPTY": "",
        "WITH_EQUALS": "a=b=c",
    }


def test_collect_env_vars_uses_compose_dir_dotenv_and_env_file_paths(tmp_path):
    """The collector pulls from ``<compose_dir>/.env`` and from each
    container's ``env_file`` paths, with the latter taking precedence.
    """
    from frankinception.compose_parser import parse_compose
    from frankinception.compose_vars import collect_env_vars

    (tmp_path / ".env").write_text("FOO=from_dot_env\nSHARED=lower\n")
    (tmp_path / "extra.env").write_text("SHARED=higher\nNEW=only_here\n")

    text = (
        "services:\n"
        "  app:\n"
        "    image: foo\n"
        "    env_file: extra.env\n"
    )
    containers = parse_compose(text)
    out = collect_env_vars(tmp_path, containers)
    assert out["FOO"] == "from_dot_env"
    assert out["SHARED"] == "higher"  # extra.env overrides .env
    assert out["NEW"] == "only_here"


def test_compose_parser_captures_env_file():
    from frankinception.compose_parser import parse_compose

    text = (
        "services:\n"
        "  a:\n"
        "    image: foo\n"
        "    env_file: extra.env\n"
        "  b:\n"
        "    image: bar\n"
        "    env_file:\n"
        "      - one.env\n"
        "      - two.env\n"
    )
    containers = parse_compose(text)
    by_name = {c.name: c for c in containers}
    assert by_name["a"].env_file_paths == ["extra.env"]
    assert by_name["b"].env_file_paths == ["one.env", "two.env"]


def test_docker_run_captures_env_file_flag():
    from frankinception.compose_parser import parse_docker_run

    c = parse_docker_run("docker run --env-file ./prod.env -d nginx:alpine")
    assert c.env_file_paths == ["./prod.env"]



def test_suggest_vault_key_from_compose_var():
    """``${POSTGRES_PASSWORD}`` should suggest ``vault_postgres_password``,
    and the helper should be idempotent if the var already starts with
    ``VAULT_`` (so we don't get ``vault_vault_foo``).
    """
    from frankinception.screens.var_resolver import _VariableEditorScreen

    suggest = _VariableEditorScreen._suggest_vault_key
    assert suggest("POSTGRES_PASSWORD") == "vault_postgres_password"
    assert suggest("DB_PW") == "vault_db_pw"
    assert suggest("VAULT_API_TOKEN") == "vault_api_token"
    assert suggest("vault_existing") == "vault_existing"



# ---- docker catalog state helpers -------------------------------------------


def _build_docker_state(tmp_path):
    """Spin up a minimal AppState with a populated docker catalog."""
    from frankinception.paths import Layout
    from frankinception.state import AppState
    from frankinception.inventory import Inventory

    inv_dir = tmp_path / "inv"
    (inv_dir / "group_vars" / "docker").mkdir(parents=True)
    (inv_dir / "group_vars" / "all").mkdir(parents=True)
    (inv_dir / "host_vars").mkdir()
    hosts = inv_dir / "hosts.yml"
    hosts.write_text("docker:\n  hosts: {}\n")

    cat = inv_dir / "group_vars" / "docker" / "docker_catalog.yml"
    cat.write_text(
        "docker_containers_catalog:\n"
        "  alpha:\n"
        "    image: nginx:latest\n"
        "    ports: ['80:80']\n"
        "  beta:\n"
        "    image: redis:7\n"
        "docker_groups_catalog:\n"
        "  public:\n"
        "    alpha:\n"
        "  cache:\n"
        "    beta:\n"
    )
    layout = Layout(
        project_root=tmp_path,
        inventory_dir=inv_dir,
        plays_dir=tmp_path / "plays",
        ansible_cfg=None,
    )
    return AppState.load(layout)


def test_state_docker_helpers_query_and_mutate(tmp_path):
    state = _build_docker_state(tmp_path)
    assert set(state.docker_containers().keys()) == {"alpha", "beta"}
    assert state.container_groups_for("alpha") == ["public"]
    assert state.container_groups_for("beta") == ["cache"]

    # Move alpha into cache as well.
    state.set_container_groups("alpha", ["public", "cache"])
    assert state.container_groups_for("alpha") == ["cache", "public"]

    # Rename beta → bee, group memberships follow.
    state.rename_container("beta", "bee")
    assert "bee" in state.docker_containers()
    assert "beta" not in state.docker_containers()
    assert state.container_groups_for("bee") == ["cache"]
    assert state.container_groups_for("beta") == []

    # Delete alpha, drops from public group.
    state.delete_container("alpha")
    assert "alpha" not in state.docker_containers()
    assert "alpha" not in state.docker_groups()["public"]

    # Save and reload — changes should round-trip through ruamel.
    state.save_docker_catalog()
    from frankinception import yaml_io

    reloaded = yaml_io.load(state.docker_catalog_path)
    assert "alpha" not in reloaded["docker_containers_catalog"]
    assert "bee" in reloaded["docker_containers_catalog"]
    assert "alpha" not in reloaded["docker_groups_catalog"]["public"]


def test_state_rename_existing_target_raises(tmp_path):
    state = _build_docker_state(tmp_path)
    import pytest as _pytest

    with _pytest.raises(KeyError):
        state.rename_container("alpha", "beta")
    with _pytest.raises(KeyError):
        state.rename_docker_group("public", "cache")


def test_state_delete_docker_group(tmp_path):
    state = _build_docker_state(tmp_path)
    state.delete_docker_group("public")
    assert "public" not in state.docker_groups()
    # The container still exists; only its membership goes away.
    assert "alpha" in state.docker_containers()
    assert state.container_groups_for("alpha") == []



# ---- play runner output hardening ------------------------------------------


def test_run_output_sanitised_env_keeps_unbuffered_and_lets_color_through(tmp_path):
    """The play-runner output forces Python unbuffered for prompt streaming
    but no longer suppresses color — under a real PTY we want Ansible's
    colour codes coming through so ``Text.from_ansi`` can render them.
    """
    from frankinception import runner
    from frankinception.screens.play_runner import _RunOutputScreen

    play = tmp_path / "play.yml"
    play.write_text("- hosts: all\n  tasks: []\n")
    inv = runner.build(playbook=play, project_root=tmp_path, inventory_dir=tmp_path)
    screen = _RunOutputScreen.__new__(_RunOutputScreen)
    screen.invocation = inv
    env = screen._sanitised_env()
    assert env.get("PYTHONUNBUFFERED") == "1"
    # Colour-suppression was removed deliberately — these should not be set
    # by us. The user can still set them in the parent shell to opt out.
    assert "ANSIBLE_FORCE_COLOR" not in env or env["ANSIBLE_FORCE_COLOR"] != "0"
    assert "NO_COLOR" not in env or env["NO_COLOR"] != "1"


def test_run_output_safe_write_silent_when_unmounted(tmp_path):
    """``_safe_write`` must return cleanly even with no widget attached.

    This is the path that keeps the worker from crashing when the user
    Esc-backs out of the run screen mid-run. We also confirm the plain
    mirror is still appended to so a "save log" after Esc-back still
    has the output.
    """
    from frankinception import runner
    from frankinception.screens.play_runner import _RunOutputScreen

    play = tmp_path / "play.yml"
    play.write_text("- hosts: all\n  tasks: []\n")
    inv = runner.build(playbook=play, project_root=tmp_path, inventory_dir=tmp_path)
    screen = _RunOutputScreen.__new__(_RunOutputScreen)
    screen.invocation = inv
    screen._unmounted = True
    screen._plain_output = []
    # Should not raise — the early-return for unmounted is the whole point.
    screen._safe_write("anything")
    assert screen._plain_output == ["anything"]
