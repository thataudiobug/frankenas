mp0: /Yasha/Media/movies/,mp=/movies
mp1: /jester/media/tv/,mp=/tv 
mp2: /jester/media/meta,mp=/meta
lxc.cgroup2.devices.allow: c 226:128 rwm
lxc.mount.entry: /dev/dri/renderD128 dev/dri/renderD128 none bind,optional,create=file
lxc.hook.pre-start: sh -c "chown 100000:100108 /dev/dri/renderD128" 
