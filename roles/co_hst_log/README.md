co_hst_log
==========

Fleet-wide **log shipping** via [Grafana Alloy](https://grafana.com/docs/alloy/).
Runs on every host (sibling to `co_hst_sec`) and forwards logs to the central
Loki on **Mollymauk** over the **OOB** management network.

Alloy is the supported successor to Promtail (now deprecated). One agent
collects journald, Docker container logs, and arbitrary files.

Source selection (catalog -> enabled -> overrides)
--------------------------------------------------

`log_sources_catalog` (in `group_vars/all`) defines named source profiles.
A host lists extras in `log_sources_enabled`; the `base` profile (journald)
is **always** shipped, so the default is journald-only.

```yaml
# host_vars/<docker host>.yml
log_sources_enabled: [docker]

# host_vars/<npm host>.yml
log_sources_enabled: [npm]      # tails NPM's bind-mounted proxy logs

# host_vars/pve.yml
log_sources_enabled: [pve]      # adds /var/log/pve/tasks
```

What ships
----------

- **journald** (every host): sshd, dnsmasq, systemd units, kernel.
- **Docker** (`docker` profile): container logs via the local daemon socket
  (Alloy's user is added to the `docker` group).
- **Files** (`npm`, `pve`, or custom): explicit paths, e.g. NPM access/error
  logs on the proxy, PVE task logs on the node.

Every stream is labelled with `host=<inventory_hostname>` (plus anything in
`co_log_extra_labels`) so you can filter and correlate in Grafana.

Network & safety
----------------

- Ships to `co_log_loki_host` (`mollymauk.oob.frankenas.lan`) on `:3100`
  over **OOB** — log traffic never touches WAN, and it works through the
  `dl_vpn_client` kill switch (the mgmt CIDR bypasses the tunnel).
- The log server sets `co_log_enabled: false` for itself to avoid a
  ship-to-self loop before Loki is up.
- `verify.yml` probes Loki's `/ready` over OOB and warns (doesn't fail) if
  the path is down, so logs aren't silently dropped.

Key variables
-------------

- `co_log_enabled` (default `true`) — master switch.
- `co_log_loki_host` / `co_log_loki_port` — central Loki endpoint (OOB).
- `log_sources_enabled` / `log_sources_overrides` — per-host sources.
- `co_log_extra_labels` — extra static labels on every stream.

License
-------

MIT
