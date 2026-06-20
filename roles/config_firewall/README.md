config_firewall
===============

Firewalling applied at the **highest available enforcement layer** for each
host. One role, three internal code paths, selected automatically by
`tasks/main.yml`:

| Host type | Layer | Where rules are enforced |
|-----------|-------|--------------------------|
| Proxmox guest (`pct`/`qemu`) | `guest_pve.yml` | host-side of the guest veth, on the `pve` node (`/etc/pve/firewall/<vmid>.fw`) |
| Proxmox node (`hosts_baremetal`) | `node_pve.yml` | datacenter `cluster.fw` + node `host.fw` |
| Other baremetal | `baseline_nft.yml` | in-guest `nftables inet filter` |

This consolidates the three firewall roles that existed before the refactor
(`pve_fw`, `es_hst_pve`, and the in-guest nftables piece of `co_hst_sec`).

Why enforce on the hypervisor for guests?
-----------------------------------------

Proxmox enforces guest rules on the **host side** of the guest's `veth` in
the host kernel, before traffic reaches the guest. Root inside a compromised
container therefore **cannot** flush or edit them — unlike in-guest
`nftables`, which is enforced by the very kernel an attacker would control.
Management is centralized on `pve` and survives guest rebuilds.

Variable namespaces
-------------------

Variables are prefixed by the layer they configure:

- `config_firewall_guest_*` — Proxmox per-guest `.fw` (API connection,
  profile selection, mgmt-SSH injection).
- `config_firewall_node_*` / `config_firewall_node_dc_*` — Proxmox node
  host.fw and the datacenter `cluster.fw` master switch.
- `config_firewall_*` — the in-guest nftables baseline (allowlist, ports,
  ICMP, service management).

Lockout safety
--------------

Proxmox firewalling is **per-NIC**. The `network_catalog` enables it on the
**WAN** NIC (`firewall: 1`) and disables it on the **OOB** management NIC
(`firewall: 0`). So a profile's `policy_in: DROP` only governs WAN ingress —
SSH / Ansible / Proxmox API over `10.128.0.0/9` is never filtered and cannot
be locked out. As belt-and-suspenders, the guest layer auto-injects an
`IN ACCEPT` for SSH from `config_firewall_guest_mgmt_cidr` (toggle with
`config_firewall_guest_inject_mgmt_ssh`).

The in-guest baseline likewise always allows SSH from
`config_firewall_mgmt_cidr` before its drop policy, and the node layer
preflights that SSH (22) is in `config_firewall_node_mgmt_ports` before
applying a DROP policy.

Selection — guest layer (catalog -> enabled -> overrides)
---------------------------------------------------------

```yaml
# host_vars/<host>.yml
firewall_enabled: edge          # names a firewall_catalog entry
firewall_overrides:             # optional per-host tweaks (recursive merge)
  rules:
    - { type: in, action: ACCEPT, source: "10.0.0.0/9", dport: 9090, proto: tcp }
```

A guest that doesn't set `firewall_enabled` is skipped. The `firewall_catalog`
(a group_var) holds the profiles; each rule maps to a Proxmox `[RULES]` line
(`type`, `action`, `source`, `dest`, `proto`, `dport`, `sport`, `iface`,
`log`, `comment`).

Prerequisites
-------------

- The **datacenter (cluster) firewall must be enabled** (`cluster.fw`
  `enable: 1`) or guest rules are inert. The node layer (`node_pve.yml`)
  owns that switch; the guest layer only checks and warns
  (`config_firewall_guest_check_datacenter_enabled`).
- The guest's **WAN NIC must have `firewall: 1`** in the resolved netif
  (set in `network_catalog`); the OOB NIC stays `firewall: 0`.
- `community.proxmox` collection (pinned in `requirements.yml`).

License
-------

MIT
