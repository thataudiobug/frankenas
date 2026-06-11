dl_ldap_srv
===========

Stands up [LLDAP](https://github.com/lldap/lldap) — a lightweight LDAP
directory with a friendly **web UI for user/group management** — as the user
store behind Authelia. Co-locates with Authelia + Redis on **Orthax** (the
identity stack).

Why LLDAP (and why now)
-----------------------

The Authelia *file* backend can't offer permanent, self-service password
changes without config drift (Ansible re-renders the user file and reverts
them). A directory fixes that at the source: LLDAP owns users/passwords, so:

- Users change their own password **permanently** via LLDAP's UI, or via
  Authelia's password-reset (which writes through to LLDAP).
- You create/manage users and groups in a **web GUI**, not a vault-templated
  YAML file.
- Authelia points at LLDAP via its built-in `implementation: 'lldap'`
  backend — no attribute mapping to hand-maintain.

Config model
------------

LLDAP is configured **entirely by `LLDAP_*` environment variables** (no
config file), so the real config lives in the container env in
`docker_containers_catalog`. This role lays out the `/data` dir on the config
bind and verifies the directory answers. Key env (set from vault):

- `LLDAP_JWT_SECRET` — signs LLDAP web sessions.
- `LLDAP_KEY_SEED` — derives the directory key material.
- `LLDAP_LDAP_BASE_DN` — `dc=frankenas,dc=com`.
- `LLDAP_LDAP_USER_PASS` — the LLDAP admin (UI) password.

First-boot manual step
----------------------

After the container is up, in the LLDAP web UI (`:17170`, OOB-only):

1. Create the **`authelia_bind`** service account.
2. Grant it the **`lldap_password_manager`** permission (needed so Authelia
   can change user passwords; a read-only bind only allows login checks).
3. Set its password to match `ldap_srv_bind_password` (vault) — this is what
   `dl_auth_srv` binds with.
4. Create your user **groups** (e.g. `admins`, `media`) and users.

These can't be fully pre-seeded declaratively here (LLDAP has a bootstrap
script for that as a later enhancement); the verify step prints the reminder.

Network & break-glass
----------------------

- LDAP (`3890`) and web UI (`17170`) are published on the **OOB** anchor only
  and firewalled to OOB — the directory is never WAN-reachable.
- Break-glass holds: the directory gates **web app** auth via Authelia. SSH
  and recovery-path host access stay key-based and independent of LDAP, so a
  broken directory never locks you out of the means to fix it.

License
-------

MIT
