pve_fw
======

**Hypervisor-enforced** per-guest firewalling for droplets running under
Proxmox. Renders a guest's Proxmox firewall file
(`/etc/pve/firewall/<vmid>.fw`) on the `pve` node from a named policy
profile.

Why on the hypervisor instead of in the guest?
-----------------------------------------------

Proxmox enforces these rules on the **host side** of the guest's `veth`/`tap`
interface, in the host kernel, before traffic reaches the guest. Root inside
a compromised container therefore **cannot** flush or edit them — unlike
in-guest `nftables`, which is enforced by the very kernel an attacker would
control. Management is also centralized on `pve` and survives guest rebuilds.

This is why droplets defer their in-guest firewall (`co_hst_sec`'s
`co_fw_enabled` is off for the `pct` group) and let this role enforce ingress
instead. SSH hardening and unattended upgrades stay in `co_hst_sec` (those
are guest-internal and can't be done from the hypervisor).

Lockout safety
--------------

Proxmox firewalling is **per-NIC**. The `network_catalog` enables it on the
**WAN** NIC (`firewall: 1`) and disables it on the **OOB** management NIC
(`firewall: 0`). So a profile's `policy_in: DROP` only governs WAN ingress —
SSH / Ansible / Proxmox API over `10.128.0.0/9` is never filtered and cannot
be locked out. As belt-and-suspenders, the role also auto-injects an
`IN ACCEPT` for SSH from `pve_fw_mgmt_cidr` (toggle with
`pve_fw_inject_mgmt_ssh`).

Selection (catalog -> enabled -> overrides)
-------------------------------------------

```yaml
# host_vars/<host>.yml
firewall_enabled: edge          # names a firewall_catalog entry
firewall_overrides:             # optional per-host tweaks (recursive merge)
  rules:
    - { type: in, action: ACCEPT, source: "10.0.0.0/9", dport: 9090, proto: tcp }
```

A host that doesn't set `firewall_enabled` is skipped.

Profile shape (`firewall_catalog`, a group_var)
------------------------------------------------

```yaml
firewall_catalog:                 # pick one
  edge:                           # WAN-facing reverse proxy
    policy_in: DROP
    policy_out: ACCEPT
    rules:
      - { type: in, action: ACCEPT, dport: "80,443", proto: tcp, comment: "HTTP(S) from WAN" }
  internal:                       # no public ingress at all
    policy_in: DROP
    policy_out: ACCEPT
    rules: []
```

Each rule maps to a Proxmox `[RULES]` line. Supported keys: `type`
(`in`/`out`/`forward`/`group`, default `in`), `action` (required:
`ACCEPT`/`DROP`/`REJECT` or a security-group name), `source`, `dest`,
`proto`, `dport`, `sport`, `iface`, `log`, `comment`.

Prerequisites
-------------

- The **datacenter (cluster) firewall must be enabled** (`cluster.fw`
  `enable: 1`) or guest rules are inert. That's owned by the PVE host
  workflow (`es_hst_pve`), not this role; `pve_fw` only checks and warns
  (`pve_fw_check_datacenter_enabled`).
- The guest's **WAN NIC must have `firewall: 1`** in the resolved netif
  (set in `network_catalog`); the OOB NIC stays `firewall: 0`.
- `community.proxmox` collection (pinned in `requirements.yml`).

Where it runs
-------------

The role targets droplets but delegates its work to `pve` (the `.fw` files
live on pmxcfs there). It reuses `was_built.vmid` when run right after
`pve_dl`, or looks the vmid up by hostname for stand-alone runs.

License
-------

MIT
