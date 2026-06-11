dl_auth_srv
===========

Prepares the central **authentication / SSO server** (Orthax): renders
[Authelia](https://www.authelia.com/) + Redis config onto bind storage so the
Docker stack can mount it. Authelia is the **auth/SSO + authorization** layer
and gates web apps at the NPM reverse proxy via forward auth — apps need no
changes of their own. Users live in **LLDAP** (see `dl_ldap_srv`), so Authelia
authenticates against the directory via its LDAP backend.

Division of labour (same as every other service droplet):

- **`pve_dl`** builds the LXC (s8 OS) on the reserved identity anchor
  `10.128.0.4`, applies the `config` bind, and the `identity` firewall profile.
- **`dl_ldap_srv`** prepares the LLDAP directory (the user store).
- **`dl_auth_srv`** (this role) renders `configuration.yml` (LDAP backend) +
  secrets from the vault.
- **`docker` role** deploys the `lldap` + `authelia` + `redis` containers
  (group `identity`).
- **NPM forward-auth wiring** (`es_svc_rp`) gates each protected proxy host,
  with a per-host toggle so it's opt-in and reversible (break-glass).

User store: LLDAP (LDAP backend)
--------------------------------

Users and groups live in **LLDAP**, managed via its web UI — not a file here.
Authelia binds to LLDAP using the built-in `implementation: 'lldap'` template
(all attribute/filter mappings handled automatically). This gives users
**permanent, self-service password changes** (via Authelia's password-reset
or the LLDAP UI) with no config drift. The bind account
(`auth_srv_ldap_bind_user`) must hold the `lldap_password_manager` permission
in LLDAP for Authelia-driven password changes to work.

Access control model
---------------------

`authelia_access_catalog` (group_var) is a list of rules, evaluated
top-to-bottom, **first match wins**, with `default_policy: deny`:

```yaml
authelia_access_catalog:
  - { domain: 'jellyfin.frankenas.com', subject: 'group:media',  policy: 'one_factor' }
  - { domain: 'radarr.frankenas.com',   subject: 'group:admins', policy: 'two_factor' }
```

- **Per-subdomain authorization**: a `media`-only user gets Jellyfin and is
  denied Radarr (no matching rule → falls through to deny).
- **Per-resource factor level**: `policy` is set on the *rule*, not the user.
  `two_factor` resources force TOTP enrolment/prompt; users who only ever
  reach `one_factor` resources are never prompted for TOTP. Reaching a
  `two_factor` resource step-elevates the existing SSO session.

Behind Cloudflare
-----------------

No Cloudflare-side config needed. Authelia derives the real client IP from
the `X-Forwarded-For` NPM sends; the session cookie is scoped to
`auth_srv_root_domain` so SSO spans every `*.frankenas.com` app.

Secrets (set from vault — role refuses placeholders)
----------------------------------------------------

- `auth_srv_session_secret` — signs/encrypts the session cookie.
- `auth_srv_storage_encryption_key` — encrypts TOTP secrets at rest (>20 chars).
- `auth_srv_jwt_secret` — signs password-reset links.
- `auth_srv_redis_password` — Authelia↔Redis auth.
- `auth_srv_ldap_bind_password` — Authelia↔LLDAP bind (== `ldap_srv_bind_password`).

Break-glass
-----------

Authelia gates the **web** path only. SSH / console access to recovery-path
hosts (`pve`, `dns-oob`, `c2`, Orthax itself) stays key-based and
independent, so a broken Authelia never locks you out of the means to fix it.
Do not put OOB management surfaces behind it.

License
-------

MIT
