es_svc_rp
=========

Reverse-proxy (NPM) host configuration. Currently: the **Authelia
forward-auth integration** — drops two reusable nginx snippets where NPM can
`include` them, so each proxy host can be gated behind Authelia by adding two
lines in its NPM **Advanced** config.

Why snippets + manual include
-----------------------------

NPM owns and rewrites `/data/nginx/proxy_host/*.conf`, so editing those
directly isn't idempotent. The supported pattern is reusable snippet files
referenced from each proxy host's Advanced tab. This makes protection
**opt-in and reversible per service** (delete the two lines) — which
preserves the break-glass property: you can drop Authelia in front of an app,
or take it back off, without Authelia needing to be up.

Files dropped (under `{{ rp_npm_data_path }}/nginx/custom/`, visible inside
the container at `/data/nginx/custom/`):

- `authelia-authrequest.conf` — the internal `/authelia` subrequest endpoint
  (server scope, include once per protected host).
- `authelia-location.conf` — the `auth_request` enforcement + portal redirect
  (location scope, include inside the location block).

Protecting a proxy host
------------------------

In NPM, edit the proxy host → **Advanced** tab, add:

```nginx
include /data/nginx/custom/authelia-authrequest.conf;

location / {
    include /data/nginx/custom/authelia-location.conf;
    # ... NPM's existing proxy_pass etc. ...
}
```

Authorization (which group, one_factor vs two_factor) is decided by
**Authelia's** `authelia_access_catalog` keyed on the request domain, not
here. This snippet just enforces whatever Authelia decides.

Behind Cloudflare
-----------------

The snippet forwards `X-Forwarded-For` so Authelia sees the real client IP —
no Cloudflare-side config needed.

Key variables
-------------

- `rp_npm_data_path` (`/mnt/config/npm/data`) — NPM's bind-mounted `/data`.
- `rp_authelia_internal_url` — Authelia's OOB endpoint NPM calls.
- `rp_authelia_portal_url` — public portal redirect target.
- `rp_authelia_enabled` — master toggle.

License
-------

BSD
