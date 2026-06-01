# frankinception

TUI utility for managing the frankenas Ansible inventory over SSH.

## Install

The tool lives alongside the Ansible code at `frankenas/scripts/frankinception`,
so a single clone of the frankenas repo brings it along. From the C2 host:

```bash
python3 -m venv ~/.venvs/frankinception
~/.venvs/frankinception/bin/pip install -e ~/frankenas/scripts/frankinception
~/.venvs/frankinception/bin/frankinception
```

Or with pipx:

```bash
pipx install ~/frankenas/scripts/frankinception
frankinception
```

## Pointing at an inventory

By default the tool reads `ansible.cfg` from the current directory (or the
nearest parent), follows its `inventory =` setting, and edits the YAML files
under that path.

Override with an explicit path:

```bash
frankinception --inventory ~/frankenas/inventories/prod
```

## What it does

* Lists hosts from `hosts.yml` and shows their group membership.
* For each host, edits `host_vars/<host>.yml`:
  * Toggle group membership in `hosts.yml` (parent groups follow children).
  * Pick values from any `*_catalog.yml` discovered under the host's groups
    (compute, storage, network, accelerator, OS, users, docker groups).
  * Per-container overrides for docker hosts.
* Imports `docker run` lines and `docker-compose.yml` services into
  `docker_containers_catalog`, mapping host paths against `docker_bind_catalog`
  (and prompting to add new binds when no match is found).
* Lists playbooks under `plays/` with descriptions and runs the selected one,
  optionally limited to the current host.

## Play descriptions

Each playbook is described by:

1. The first line if it is a comment (e.g. `# Deploy the docker fleet`).
2. Otherwise the concatenated `name:` fields of each play in the file.

## Keys

* `Tab` / `Shift+Tab` — move focus between panels
* `Enter` — open / select
* `Space` — toggle a checkbox
* `s` — save the current host
* `q` — quit (prompts if there are unsaved edits)
