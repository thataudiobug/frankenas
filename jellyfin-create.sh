#!/bin/bash
echo "Welcome to Casey's Port Royal LXC spinnup script."

# Default vars
var_name="watch"
var_cpus="6"
var_mem="4096"
var_swap="512"
var_fsvol="caleb"
var_fsgb="8"
var_wan_bridge="vmbr1"
var_inet_bridge="vmbr0"
var_gwid="254"
var_dns_local="254"

# Get the ct ID
read -p 'Please enter this containers ID: ' var_cid

# Function for configuring network info
config_network () {
  read -p 'Please enter the Wan Bridge ID (vmbr1): ' var_wan_bridge
  read -p 'Please enter the Inet Bridge ID (vmbr0): ' var_inet_bridge
  read -p 'Please enter the gateway ID: ' var_gwid
  read -p 'Please enter the local DNS ID: ' var_dns_local
}

# Function for configuing everything else
config_all_else () {
  read -p 'Please enter the CT name: ' var_name
  read -p 'Please enter the CPU count: ' var_cpus
  read -p 'Please enter the memory in MBs: ' var_mem
  read -p 'Please enter the swap in MBs: ' var_swap
  read -p 'Please enter the storage where the root file system should live: ' var_fsvol
  read -p 'Please enter the root file system size in GBs: ' var_fsgb
  config_network
}

# Fucntion to check if shit has changed.
echo 'Do any settings need manual input?'
select var_install_type in "None" "Networking" "All" 
do
    case $var_install_type in
        None) break;;
        Networking) 
          config_network break;;
        All)
          config_all break;;
    esac
done

# Build the CT
pct create $var_cid local:vztmpl/ubuntu-24.04-standard_24.04-2_amd64.tar.zst \
  --hostname $var_name \
  --cores $var_cpus \
  --memory $var_mem \
  --swap $var_swap \
  --rootfs $var_fsvol:$var_fsgb \
  --net0 name=eth0,bridge=$var_wan_bridge,ip=192.168.1.$var_cid/24,gw=192.168.1.$var_gwid \
  --net1 name=eth1,bridge=$var_inet_bridge,ip=192.168.8.$var_cid/24 \
  --nameserver 192.168.1.$var_dns_local,1.1.1.1,8.8.8.8 \
  --onboot 1 \
  --features nesting=1 \
  --unprivileged 1 \
  --ssh-public-keys <(curl -fsSL https://github.com/thataudiobug.keys) \
  --mp0 /essek/media/,mp=/content \
  --mp1 /Caleb/docker/configs/jellyfin/config,mp=/etc/jellyfin \
  --mp2 /Caleb/docker/configs/jellyfin/data,mp=/var/lib/jellyfin \
  --mp3 /Caleb/docker/configs/jellyfin/cache,mp=/var/cache/jellyfin

# set timestamp
var_time_local= date "+%Y-%m-%d %H:%M:%S" > /dev/null
var_time_utc= TZ=UTC date "+%Y-%m-%d %H:%M:%S" > /dev/null
pct set $var_cid \
  --description "This container was last rebuilt on $var_time_UTC UTC, $var_time_local local."

# Start CT
echo "Starting container..."
pct start $var_cid

# Provision basics
pct exec $var_cid -- bash -c '
  echo "Fetching updates..." && \
  apt update && \
  echo "Installing updates..." && \
  apt upgrade -y && \
  echo "Installing curl... " && \
  apt install curl -y && \
  echo "Fetching provisioning script... " && \
  curl -o script.sh https://raw.githubusercontent.com/thataudiobug/frankenas/refs/heads/main/jellyfin-provision.sh && \
  echo "Setting script perms... " && \
  chmod 777 script.sh && \
  echo "Running provisioning script..." && \
  ./script.sh '

echo "Provisioning script... Done"

# Pull renderer ID and set perms ie: = render:x:108:jellyfin #
var_renderer=$(pct exec $var_cid -- bash -c 'getent group render | cut -d: -f3')

# add transcoding links
echo "lxc.cgroup2.devices.allow: c 226:128 rwm" >> /etc/pve/lxc/$var_cid.conf
echo "lxc.mount.entry: /dev/dri/renderD128 dev/dri/renderD128 none bind,optional,create=fi>" >> /etc/pve/lxc/$var_cid.conf
echo "lxc.hook.pre-start: sh -c \"chown 100000:10${var_renderer} /dev/dri/renderD128\"" >> /etc/pve/lxc/${var_cid}.conf

pct reboot $var_cid

# check current codecs #
pct exec -- bash -c ' /usr/lib/jellyfin-ffmpeg/vainfo --display drm --device /dev/dri/renderD128 '

echo "Build complete! Please come again."


