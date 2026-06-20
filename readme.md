Welcome to my little cave of code. This is my playground for testing
Infra-as-code stuffs for my homelab. Trust nothing you find here as even I know
not how it works.

---

# frankenas

Ansible-managed homelab built on a Proxmox hypervisor. Hosts are mostly LXC
"droplets" (plus a QEMU VM or two) provisioned and configured declaratively,
with a small statically-pinned "recovery path" (the PVE node, the DNS/DHCP
server, and the C2 control node) that can bootstrap and repair the fleet even
when core services are down.

## Layout

```
frankenas/
├── ansible.cfg              # inventory + roles paths, SSH settings
├── requirements.yml         # pinned Galaxy collections
├── inventories/prod/        # the only environment today
│   ├── hosts.yml            # groups + host membership
│   ├── group_vars/          # cross-cutting vars + catalogs (see below)
│   └── host_vars/           # per-host selections + overrides
├── plays/                   # playbooks (site.yml is the orchestrator)
├── roles/                   # functionality-focused roles (see taxonomy)
├── legacy/                  # old .sh scripts, retained for reference only
└── scripts/frankinception/  # TUI for editing the inventory (see its README)
```

## Role taxonomy

Roles are named for the **function** they provide, not the software or host
type, and use a verb/noun prefix:

- **`provision_*`** — create VMs/guests. `provision_proxmox` routes internally
  to LXC (`pct`) or QEMU (`qemu`) as needed.
- **`config_*`** — configuration concerns applied to a host: `config_basic`
  (keys, python, apt, unattended upgrades), `config_hardening` (SSH lockdown),
  `config_time` (chrony), `config_firewall` (applies at the highest available
  layer — Proxmox guest veth, Proxmox node, or in-guest nftables),
  `config_logging` (Alloy → Loki), `config_vpn_client` (OpenVPN kill switch).
- **`service_*`** — applications: `service_docker`, `service_dns` (dnsmasq),
  `service_loki` + `service_grafana` (observability), `service_authelia` +
  `service_lldap` (identity), `service_reverse_proxy` (NPM forward-auth).

## The catalog → enabled → overrides convention

Configuration is data-driven. A **catalog** (`<name>_catalog`) defines named
profiles; a host opts in with `<name>_enabled` in its `host_vars`; an optional
`<name>_overrides` tweaks the selected profile. Example:

```yaml
# in a role's defaults (or group_vars): the menu
compute_catalog:        # pick one
  small:  { cores: 2, memory_mb: 2048 }
  large:  { cores: 6, memory_mb: 4096 }

# in host_vars/<host>.yml: the choice
compute_enabled: small
```

A marker comment on the catalog key (`# pick one`, `# pick many`,
`# reference`) declares how it's selected. **Role-owned** catalogs live in the
owning role's `defaults/` (e.g. `firewall_catalog` in `config_firewall`);
**cross-cutting** catalogs that aren't owned by one role
(`users_catalog`, `droplet_bind_catalog`) stay in `group_vars/`. The
`frankinception` TUI discovers both and lets you edit host selections without
hand-editing YAML.

## Networking model

Two NICs per droplet: a **WAN** NIC (default route, firewalled) and an
**OOB** management NIC on `10.128.0.0/9` (unfiltered, how Ansible/C2 reach the
host — the lockout safety net). dnsmasq leases OOB addresses and
auto-registers `<hostname>.oob.frankenas.lan`, so `ansible_host` can be a
stable FQDN. Recovery-path anchors (pve `.1`, dns `.2`, c2 `.3`, identity `.4`,
log server `.5`) are statically pinned below the DHCP pool.

## Running it

```bash
ansible-galaxy collection install -r requirements.yml   # first time
ansible-playbook plays/site.yml                          # converge everything
ansible-playbook plays/config_log_srv.yml --limit mollymauk   # one service
```

`site.yml` imports the per-service `config_*` plays in dependency order. Most
plays expect the vault password available so service secrets resolve.
