Role Name
=========

A brief description of the role goes here.

Requirements
------------

Any pre-requisites that may not be covered by Ansible itself or the role should be mentioned here. For instance, if the role uses the EC2 module, it may be a good idea to mention in this section that the boto package is required.

Role Variables
--------------

A description of the settable variables for this role should go here, including any variables that are in defaults/main.yml, vars/main.yml, and any variables that can/should be set via parameters to the role. Any variables that are read from other roles and/or the global scope (ie. hostvars, group vars, etc.) should be mentioned here as well.

Networking
----------

A host selects networks with `network_enabled` (paired with the
`network_catalog`). It accepts a scalar (`wan`), a list (`[wan, oob]`), or a
mapping with per-NIC overrides:

    network_enabled:
      wan:
        host: 103          # -> catalog prefix + octet (192.168.1.103)
      oob:
        ip: 10.128.0.42    # explicit full address
      guest:
        dhcp: true         # DHCP instead of a static address

Per-NIC address resolution, in precedence order:

1. `ip: <addr>` — explicit static address (always wins, forces static).
2. `host: <octet>` — combined with the catalog entry's `network` prefix.
3. `dhcp: true` or `ip: dhcp` — DHCP; no address or gateway is assigned,
   the lease provides both. A `network_catalog` entry may also default
   `dhcp: true`, applied when the host gives no static `ip`/`host`.
4. The connection NIC (the `oob`-flagged network, or the gateway NIC on a
   flat host) defaults to `ansible_host`.

A static network with none of the above fails the build with a clear
message. DHCP networks and the connection NIC are exempt from that check.

The management/gateway NIC is whichever enabled network defines a `gateway`
in the catalog (at most one). The connection NIC — whose IP Ansible reaches
the box on — is the `oob`-flagged network if present, else the gateway NIC.

Dependencies
------------

A list of other roles hosted on Galaxy should go here, plus any details in regards to parameters that may need to be set for other roles, or variables that are used from other roles.

Example Playbook
----------------

Including an example of how to use your role (for instance, with variables passed in as parameters) is always nice for users too:

    - hosts: servers
      roles:
         - { role: username.rolename, x: 42 }

License
-------

BSD

Author Information
------------------

An optional section for the role authors to include contact information, or a website (HTML is not allowed).
