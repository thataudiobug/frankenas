es_hst_pve
==========

Hardens the **Proxmox VE node itself** (a baremetal host concern — guest
provisioning lives in `pve_dl`). Two responsibilities:

1. **Enables the datacenter (cluster-wide) firewall master switch.** Until
   `cluster.fw` has `enable: 1`, every per-guest `.fw` file rendered by
   `pve_fw` and the node's own `host.fw` are inert. This role owns that
   switch so guest roles don't change cluster-wide state.
2. **Restricts the node's management surfaces to OOB.** The Proxmox web UI /
   API (`:8006`), SSH (`:22`), SPICE (`:3128`) and VNC consoles are allowed
   only from `pve_node_mgmt_cidr` (`10.128.0.0/9`); WAN exposure is denied by
   a `policy_in: DROP` host policy.

SSH hardening and unattended security upgrades for the node come from
`co_hst_sec` (via `hst_base`, since PVE is a host) and are **not** duplicated
here.

Lockout safety
--------------

Preflight asserts that `pve_node_mgmt_cidr` is a valid CIDR and that SSH
(`22`) is in `pve_node_mgmt_ports` before any DROP policy is written —
otherwise the play would cut the session Ansible rides on. Proxmox also
keeps built-in defaults that avoid severing your active management session
when the firewall is first enabled. Still: **keep an out-of-band console
(IPMI / physical) available the first time you enable this on a live node.**

Key variables
-------------

- `pve_node_mgmt_cidr` (default `10.128.0.0/9`) — the only source allowed to
  reach the node's management surfaces.
- `pve_node_wan_cidr` (default `10.0.0.0/9`) — the WAN service segment
  (documentary; node services are not exposed here).
- `pve_dc_firewall_enable` (default `true`) — the cluster master switch.
- `pve_node_mgmt_ports` — ports opened to the OOB net (web UI, SSH, SPICE,
  VNC by default).
- `pve_node_cluster_peers` (default `[]`) — peer CIDRs for corosync / node
  SSH if you cluster later.

Files written
-------------

- `/etc/pve/firewall/cluster.fw` — datacenter master switch + cluster policy.
- `/etc/pve/nodes/<node>/host.fw` — node management firewall.

Both are compiled with `pve-firewall compile` after writing; a bad render
fails the play rather than being pushed to iptables.

License
-------

MIT
