dl_log_srv
==========

Prepares the central **log-aggregation server** (Mollymauk): renders the
Loki and Grafana configuration onto the host's bind-mounted storage so the
Docker stack can mount it. Loki chunk/index data lives on the **Nott 2TB
mirror** (`/mnt/nott/loki`); the OS disk stays read-mostly.

Division of labour (same as every other service droplet):

- **`pve_dl`** builds the LXC (s8 OS template) and applies the `nott` +
  `config` bind mounts.
- **`dl_log_srv`** (this role) lays out storage dirs and renders
  `loki-config.yaml` + Grafana datasource provisioning.
- **`docker` role** deploys the `loki` and `grafana` containers from
  `docker_containers_catalog` (group `observability`).
- **`co_hst_log`** runs on every *other* host to ship logs here.

Key facts
---------

- **Retention: 180 days** (`log_srv_retention_period: 4320h`), enforced by
  Loki's compactor (`retention_enabled: true`). The Nott mirror is dedicated
  to logs for now, so this is deliberately generous.
- **Loki is unauthenticated.** `auth_enabled: false` is *multi-tenancy*, not
  access control — Loki has no login. It is kept safe by (a) binding to the
  **OOB** network only and (b) a `pve_fw` profile restricting `:3100` to the
  OOB CIDR. **Never expose it to WAN.**
- **Grafana** has its own login; set `log_srv_grafana_admin_password` from
  the vault (the role refuses to run with the placeholder). Optionally front
  it with NPM later for remote access.

Key variables
-------------

- `log_srv_data_root` (`/mnt/nott/loki`) — heavy storage on the Nott bind.
- `log_srv_retention_period` (`4320h` = 180d), `log_srv_retention_enabled`.
- `log_srv_loki_http_port` (`3100`), `log_srv_grafana_port` (`3000`).
- `log_srv_grafana_admin_password` — **set from vault**.

License
-------

MIT
