config_hardening
================

Host **lockdown** — currently SSH hardening. A validated drop-in under
`/etc/ssh/sshd_config.d/`: key-only auth, no password / keyboard-interactive
login, `PermitRootLogin prohibit-password`, idle-session reaping, reduced
forwarding. Validated with `sshd -t` and applied with a **reload** (not
restart) so the live Ansible connection survives a bad config.

This role was split out of the old `co_hst_sec` baseline. Its former
siblings now live in dedicated, recyclable roles:

- **`config_basic`** — SSH keys, Python, initial apt refresh, and always-on
  unattended **security upgrades**.
- **`config_time`** — chrony time sync.
- **`config_firewall`** — all firewalling (in-guest nftables baseline plus
  the Proxmox guest/node layers).

Keeping each concern in its own role means a play composes exactly the
hardening it wants, and the no-lockout SSH preflight stays co-located with
the SSH change it guards.

Key variables
-------------

See `defaults/main.yml` for the full set. The ones you'll touch most:

- `config_hardening_mgmt_cidr` (default `10.128.0.0/9`) — management net
  allowlisted for SSH in the preflight. **Wrong value = lockout.**
- `config_hardening_ssh_harden_enabled` — master switch for SSH hardening.
- `config_hardening_ssh_permit_root_login` (default `prohibit-password`),
  `config_hardening_ssh_password_authentication` (default `no`), and the
  other `config_hardening_ssh_*` knobs.

Optional verification
---------------------

`tasks/verify.yml` re-checks the applied state (sshd parses, password auth
is off in the effective config). Run it inline by setting
`config_hardening_verify: true`, or stand-alone against a host.

Example
-------

```yaml
- hosts: all
  become: true
  roles:
    - config_basic
    - config_hardening
    - config_time
```

License
-------

MIT
