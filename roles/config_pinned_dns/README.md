# config_pinned_dns

Registers a **pinned** (static-anchor) host's DNS record on the central
dnsmasq server, so the host resolves by FQDN even though it never requests a
DHCP lease.

## The problem it solves

dnsmasq auto-registers DNS records from DHCP leases: a dynamic host boots,
requests a lease with its hostname, and dnsmasq publishes
`<hostname>.oob.frankenas.lan` automatically. But **recovery-path hosts**
(the PVE node, the DNS server, C2, the identity and log servers) use a
**static** OOB IP below the DHCP pool. They never send a DHCP request, so
dnsmasq never learns their name from a lease — their FQDN only resolves if an
explicit `host-record` exists in dnsmasq's config.

Previously that meant hand-maintaining a `static_hosts` list in
`dns_srv_catalog` and remembering to re-run the DNS play whenever an anchor
changed. This role closes that gap: each critical host registers **its own**
record when its play runs.

## How it works

- The host's static IP comes from its existing OOB NIC definition,
  `network_enabled.oob.ip` (the single source of truth it's actually built
  with) — so the published record can never drift from the real address. The
  role strips any CIDR suffix (`10.128.0.5/9` → `10.128.0.5`). An optional
  `pinned_dns_ip` host_var overrides this if a host's published IP must differ
  from its OOB NIC.
- The role runs in the host's context but **delegates** the record write to
  the DNS server (first member of the `dns_srv` group).
- It drops a per-host file `/etc/dnsmasq.d/20-pinned-<host>.conf` containing a
  single `host-record=` line, validates it with `dnsmasq --test`, and
  restarts dnsmasq — the same safety pattern `service_dns` uses.
- It **skips** cleanly when: the host has no resolvable OOB IP, no DNS server
  exists in inventory, or the host IS the DNS server (which can't register
  through itself — its anchor stays in the bootstrap `dns_srv_catalog`).

## Usage

Add the host to the `critical` group in `hosts.yml` and ensure its OOB NIC has
a static `ip` under `network_enabled.oob` (which a pinned host needs anyway),
then either:

- let it register during a build (the provisioning plays call this role,
  gated on `'critical' in group_names`), or
- run the standalone refresh play:

  ```bash
  ansible-playbook plays/register_pinned_dns.yml --limit <host>
  ```

## Key variables

- `network_enabled.oob.ip` (host_var, required for a pinned host) — the
  static OOB address; the published record is derived from this.
- `pinned_dns_ip` (host_var, optional) — override the derived IP if the
  published address must differ from the OOB NIC.
- `config_pinned_dns_domain` (default `oob.frankenas.lan`) — the zone.
- `config_pinned_dns_record` (default `inventory_hostname`) — the record label.
- `config_pinned_dns_server_group` (default `dns_srv`) — which group is the
  DNS server (used for the delegation target and the self-skip).

## License

MIT
