# legacy/

The old direct `.sh` provisioning scripts, kept for historical reference.
These predate the Ansible roles and are no longer used or wired into anything
— `provision_proxmox`, `config_firewall`, `config_vpn_client`, and the
`service_*` roles superseded them. Nothing in `plays/` or `roles/` calls into
this directory.
