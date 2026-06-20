# service_dns

Stands up a single **dnsmasq** LXC providing coordinated **DHCP + DNS** for
the out-of-band (OOB) management network (`vmbr0`, `10.128.0.0/9`), internal
zone `oob.frankenas.lan`. DHCP offloads IP assignment from Ansible, and
dnsmasq auto-registers each lease's hostname as
`<inventory_hostname>.oob.frankenas.lan` — so `ansible_host` can be a stable
FQDN instead of a hand-tracked static IP.

> Naming note: the role is `service_dns` even though it serves DHCP **and**
> DNS. DNS (FQDN `ansible_host`) is the user-facing goal; DHCP is the
> mechanism that feeds it.

## OOB-only DHCP scope

dnsmasq serves DHCP and DNS **only on the OOB NIC**. The main config binds to
the OOB interface via `interface={{ _dns.oob_iface }}`, `bind-interfaces`,
and `except-interface={{ _dns.wan_iface }}` (e.g. `eth0`). This is the
critical safety boundary: the WAN router keeps owning WAN DHCP, and this role
must never answer DHCP on the uplink segment, so the two can't collide.

DNS listening is likewise restricted — `listen-address=127.0.0.1,10.128.0.2`
means dnsmasq answers only on loopback and the OOB self IP. Verification
confirms DHCP is up on UDP `:67` via the OOB interface and not the WAN NIC.

## Static-anchor recovery model

A service that hands out addresses and names cannot itself depend on those
addresses and names. Three recovery-path hosts keep **hand-pinned static OOB
addresses** so the system can bootstrap and be repaired without the service
it provides:

| Host                  | OOB IP       | Why static                              |
| --------------------- | ------------ | --------------------------------------- |
| `pve`                 | `10.128.0.1` | hypervisor; recovery path               |
| `dl-dns-srv` (this CT) | `10.128.0.2` | can't lease an address from itself      |
| `c2`                  | `10.128.0.3` | must resolve/connect before DHCP works  |

These are written as authoritative `host-record=` entries, so they resolve
through dnsmasq **regardless of lease state** — they never appear in the lease
file. The DHCP pool starts at `10.128.0.10` and excludes the anchor/reserved
block `10.128.0.1`–`10.128.0.9` (`.1`–`.3` in use, `.4`–`.9` reserved for
future anchors). Everything else on the OOB network leases dynamically.

Because the anchors are reachable by literal IP and the role is
stateless/Ansible-rebuildable, the fleet can always be repaired without
depending on the service being repaired.

## Configuration: catalog → enabled → overrides

Like the rest of the project (`compute_catalog`/`compute_enabled`,
`ovpn_server_catalog`/`ovpn_server_enabled`), the server configuration lives
in a **catalog** in group_vars, a host **picks** a profile, and may
**override** individual fields:

* **`dns_srv_catalog`** (`group_vars/dns_srv/dns_srv_catalog.yml`) — named
  dnsmasq profiles. The bulk of the config (zone, OOB CIDR, DHCP pool,
  anchors, upstreams). Discoverable by frankinception (key ends in
  `_catalog`); marked `# pick one`.
* **`dns_srv_enabled`** (host_var) — the profile key this host runs, e.g.
  `dns_srv_enabled: oob`.
* **`dns_srv_overrides`** (host_var) — optional per-host field tweaks merged
  recursively over the selected profile, e.g. `{ oob_iface: eth0 }` on a
  host whose OOB NIC index differs. Usually empty.

A profile entry's fields:

| Field | Example | Purpose |
| --- | --- | --- |
| `domain` | `oob.frankenas.lan` | Authoritative internal zone |
| `oob_cidr` | `10.128.0.0/9` | OOB management CIDR this server owns |
| `oob_netmask` | `255.128.0.0` | `/9` netmask handed out via DHCP |
| `self_ip` | `10.128.0.2` | This CT's static OOB IP (DNS listen + DHCP dns-server option) |
| `oob_iface` | `eth1` | OOB NIC dnsmasq serves on |
| `wan_iface` | `eth0` | WAN NIC, excluded from DHCP/DNS |
| `dhcp_start` | `10.128.0.10` | DHCP pool start (excludes the anchor block) |
| `dhcp_end` | `10.128.255.254` | DHCP pool end |
| `dhcp_lease` | `12h` | Lease time |
| `static_hosts` | `pve→.1, dl-dns-srv→.2, c2→.3` | Authoritative anchor A records (never leased) |
| `upstream_dns` | `1.1.1.1`, `9.9.9.9` | Upstream forwarders for non-internal queries |

The only role-level (non-profile) variable is the operational toggle
`dns_srv_verify_strict` (default `true`) — hard-fail the play if DHCP/DNS
isn't healthy. The OOB NIC index is `eth1` when WAN is `net0` and `eth0`
when OOB is the only NIC — override per-host via `dns_srv_overrides` if it
differs.

## C2 resolver configuration is out of scope

This role configures the **dnsmasq server only**. It does **not** modify the
C2 control node's resolver. Pointing a client at the server is a C2
provisioning concern — keeping the role single-purpose means it never mutates
the control node.

For C2 to connect over FQDN `ansible_host` values, its resolver must answer
`*.oob.frankenas.lan` from dnsmasq (`10.128.0.2`). Preferred: **per-domain
routing via systemd-resolved**, so only the internal zone is sent to dnsmasq
and everything else uses C2's normal upstreams:

```ini
# /etc/systemd/resolved.conf.d/10-oob.conf  (on C2)
[Resolve]
DNS=10.128.0.2
Domains=~oob.frankenas.lan
```

Fallback (no systemd-resolved): a `resolv.conf` with a `nameserver` and
`search` entry.

```conf
# /etc/resolv.conf  (on C2, fallback)
nameserver 10.128.0.2
search oob.frankenas.lan
```

**This belongs to C2 provisioning (`plays/provision_c2.yml`), not this role.**

### Recovery note

While the C2 resolver is unconfigured (or misconfigured), the static anchors
stay reachable by **literal IP** — `pve` `10.128.0.1`, this CT `10.128.0.2`,
`c2` `10.128.0.3`. The fleet can still be repaired by falling back to an IP
`ansible_host` on the recovery-path hosts, then fixing the C2 resolver.

## Verification

After configuration the role runs `tasks/verify.yml`, which confirms:

- the `dnsmasq` service is active,
- it's listening for DNS on `:53` (loopback / OOB self IP),
- it's listening for DHCP on `:67`,
- a known static anchor resolves — `dig +short @127.0.0.1
  dl-dns-srv.oob.frankenas.lan` returns `10.128.0.2`.

With `dns_srv_verify_strict: true` (default) any failure aborts the play.
Separately, both config templates are gated by `dnsmasq --test` (via the
template `validate:` hook) so an invalid rendered config is never activated —
a bad config fails the play and leaves the running config in place.

## Dependencies

- **Packages (on the CT):** `dnsmasq`.
- **Collections:** `ansible.utils` (`ipaddr` filter for preflight asserts),
  declared in the project `requirements.yml`.
- **Existing logic (unchanged):** `provision_proxmox`'s `build_networks.yml` DHCP support
  and `pct.yml` hostname-from-inventory — this role relies on their current
  behavior to feed clients to the server.
