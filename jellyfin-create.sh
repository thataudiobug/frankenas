mp0: /essek/media/,mp=/content
mp1: /essek/media/meta,mp=/meta
lxc.cgroup2.devices.allow: c 226:128 rwm
lxc.mount.entry: /dev/dri/renderD128 dev/dri/renderD128 none bind,optional,create=fi>
lxc.hook.pre-start: sh -c "chown 100000:100108 /dev/dri/renderD128"
