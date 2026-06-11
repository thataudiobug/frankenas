co_hst_sec
==========

Common host **security baseline** for every Linux host in the fleet — shared
by both the droplet baseline (`dl_base`) and the baremetal baseline
(`hst_base`). It bundles three universally-applicable concerns, each
independently togglable:

1. **SSH hardening** — a validated drop-in under `/etc/ssh/sshd_config.d/`:
   key-only auth, no password / keyboard-interactive login,
   `PermitRootLogin prohibit-password`, idle-session reaping, reduced
   forwarding. Validated with `sshd -t` and applied with a **reload** (not
   restart) so the live Ansible connection survives a bad config.
2. **Host firewall** — an `nftables` `inet filter` table with a default-drop
   **input** policy and a small allowlist (loopback, established, SSH from
   the management net, optional ICMP, and operator-declared ports). Rendered,
   validated with `nft -c`, then loaded atomically with `nft -f`. This is an
   **ingress** baseline only; egress is unrestricted.
3. **Unattended security upgrades** — `unattended-upgrades` scoped to the
   *security* origins only, on apt's periodic timer. Deliberately narrower
   than `dl_base`'s blanket `apt state=latest`.

Relationship to other roles
----------------------------

- **`dl_base` (guests):** includes this role with the firewall **on** by
  default (predictable Ubuntu droplets).
- **`hst_base` (baremetal):** includes this role with the firewall **off** by
  default (Proxmox runs its own firewall, web UI on 8006, cluster links).
  SSH hardening + upgrades still apply. Opt the firewall in per host once the
  required ports are declared.
- **`dl_vpn_client` (kill switch):** owns `/etc/nftables.conf` with a
  `flush ruleset` fail-closed egress kill switch. This role's firewall
  **auto-defers** on any host in the `vpn_clients` group
  (`co_fw_enabled` defaults to `false` there) so the two never clobber each
  other.

Key variables
-------------

See `defaults/main.yml` for the full set. The ones you'll touch most:

- `co_mgmt_cidr` (default `10.128.0.0/9`) — management net allowlisted for
  SSH before the drop policy. **Wrong value = lockout.** Matches the kill
  switch's `vpn_mgmt_cidr`.
- `co_fw_enabled` — master switch for the firewall. Defaults to
  `"{{ 'vpn_clients' not in group_names }}"`.
- `co_fw_allow_ports_public` / `co_fw_allow_ports_restricted` — open service
  ports to the world or to specific CIDRs.
- `co_ssh_harden_enabled`, `co_uu_enabled` — master switches for SSH and
  upgrades.
- `co_uu_automatic_reboot` (default `false`) — auto-reboot for kernel
  patches.

Optional verification
---------------------

`tasks/verify.yml` re-checks the applied state (sshd parses, the expected
nftables table/policy is loaded, the unattended-upgrades timer is active).
Run it inline by setting `co_verify: true`, or stand-alone against a host.

Example
-------

A reverse-proxy guest exposing 80/443 to the world, metrics to mgmt only:

```yaml
- hosts: edge
  become: true
  roles:
    - role: co_hst_sec
      vars:
        co_fw_allow_ports_public:
          - { proto: tcp, port: 80 }
          - { proto: tcp, port: 443 }
        co_fw_allow_ports_restricted:
          - { proto: tcp, port: 9100, cidr: "10.128.0.0/9" }
```

License
-------

BSD
