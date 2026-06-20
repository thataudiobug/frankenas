# config_vpn_client

Configures an Ubuntu host as an OpenVPN **client** with a fail-closed
("kill-switch") firewall. WAN traffic can only leave through the VPN
tunnel; if the tunnel drops, all non-management WAN egress is blocked.

## How the kill switch works

A single `inet` nftables table (`killswitch`) with default-drop policies on
input, output, and forward. The output chain permits only:

1. loopback and established/related return traffic,
2. the management subnet (`config_vpn_client_mgmt_cidr`) — so SSH/Ansible keep working,
3. the resolved VPN endpoint IP(s) on the physical NIC — the bootstrap
   exception OpenVPN needs to build the tunnel,
4. anything out the tunnel device (`config_vpn_client_tun_dev`).

Everything else is dropped. If `tun0` goes away, rule 4 matches nothing and
traffic hits the drop policy — that is the kill switch. Because it's an
`inet` table with no IPv6 tunnel exception, IPv6 WAN egress is dropped too;
IPv6 is additionally disabled via sysctl by default.

## Server selection (catalog → enabled framework)

You don't hand-configure the endpoint. Drop one or more servers into a
catalog and select one per host with `ovpn_server_enabled` — the same
pattern as `compute_enabled` → `compute_catalog`.

`group_vars/config_vpn_client/config_vpn_client_server_catalog.yml`:

```yaml
ovpn_server_catalog:
  us-east-1:
    ovpn_file: "{{ vault_us_east_1_ovpn }}"   # full .ovpn text
    ovpn_user: "{{ vault_us_east_1_user }}"
    ovpn_pass: "{{ vault_us_east_1_pass }}"
  eu-west-1:
    ovpn_file: "{{ vault_eu_west_1_ovpn }}"
    ovpn_user: "{{ vault_eu_west_1_user }}"
    ovpn_pass: "{{ vault_eu_west_1_pass }}"
```

`host_vars/<host>.yml`:

```yaml
ovpn_server_enabled: us-east-1
```

That's the whole configuration. The endpoint host, port, and protocol are
**parsed out of the selected `.ovpn` file itself** (its `remote` /
`port` / `proto` directives), so there's nothing to keep in sync. Multiple
`remote` lines (failover) are all parsed and pinned in the kill switch.

The credentials (`ovpn_user` / `ovpn_pass`) feed the `.ovpn`'s
`auth-user-pass`. Store all three values in the vault.

## Optional / advanced variables

See `defaults/main.yml`. Notable ones:

- `config_vpn_client_mgmt_cidr` (default `10.128.0.0/9`) — management subnet allowed
  through the kill switch. Get this right or risk SSH lockout.
- `config_vpn_client_endpoint_rules_override` — pin firewall bootstrap rules manually as
  `[{ip, port, proto}, ...]` when control-node DNS differs from the target
  (geo-DNS) or DNS is unavailable.
- `config_vpn_client_dns_servers`, `config_vpn_client_disable_ipv6`, `config_vpn_client_tun_dev`, `config_vpn_client_verify_strict`.

## Endpoint resolution

The endpoint host(s) are parsed from the `.ovpn` and resolved to A records
at provision time via the `dig` lookup, which runs on the **control node**.
The kill switch is pinned to those IPs so OpenVPN can reach the server on
the physical NIC before the tunnel exists (resolving the hostname later
would require DNS, which the kill switch blocks — a chicken-and-egg). If
the control node resolves differently from the target, set
`config_vpn_client_endpoint_rules_override`.

## Verification

After setup the role runs `tasks/verify.yml`, which checks the tunnel is
up, the default route uses it, the kill-switch table is loaded with an
output-drop policy, and public egress works via the tunnel. With
`config_vpn_client_verify_strict: true` (default) any failure aborts the play.

The same checks run stand-alone via `plays/config_vpn_client_killswitch_verify.yml` for
ad-hoc or scheduled leak testing without re-applying configuration.

## Lockout safety

`config_vpn_client_mgmt_cidr` is allowed both directions before any default-deny takes
effect, and the nftables config is syntax-checked (`nft -c`) before load.
Set this variable correctly for your fleet or you risk losing SSH access
to the host.

## Dependencies

Requires the `community.general` (dig lookup) and `ansible.posix` (sysctl)
collections, plus `ansible.utils` for the `ipaddr` filter. These are
declared in the project `requirements.yml`.
