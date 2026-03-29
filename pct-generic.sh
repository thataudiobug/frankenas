#!/bin/bash
echo "Welcome to Casey's geenric LXC spinnup script."

# Default vars
var_name="unnamed"
var_cpus="6"
var_mem="8192"
var_swap="512"
var_fsvol="caleb"
var_fsgb="8"
var_wan_bridge="vmbr1"
var_inet_bridge="vmbr0"
var_gwid="254"
var_dns_local="254"

# Get the ct ID
read -p 'Please enter this containers ID: ' var_cid
read -p 'Please enter the CT name: ' var_name
read -p 'Please enter the CPU count: ' var_cpus
read -p 'Please enter the memory in MBs: ' var_mem
read -p 'Please enter the swap in MBs: ' var_swap
echo 'Please select which drive the pct should live on: '
select var_fsvol in "Nott" "caleb" 
read -p 'Please enter the root file system size in GBs: ' var_fsgb


# Function for configuring network info
config_network () {
  read -p 'Please enter the Wan Bridge ID (vmbr1): ' var_wan_bridge
  read -p 'Please enter the Inet Bridge ID (vmbr0): ' var_inet_bridge
  read -p 'Please enter the gateway ID: ' var_gwid
  read -p 'Please enter the local DNS ID: ' var_dns_local
}

# Fucntion to check if shit has changed.
echo 'Do any settings need manual input?'
select var_install_type in "None" "Networking" 
do
    case $var_install_type in
        None) break;;
        Networking) 
          config_network break;)
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
  --ssh-public-keys <(curl -fsSL https://raw.githubusercontent.com/thataudiobug/frankenas/refs/heads/main/robot.key)

# set timestamp
var_time_local= date "+%Y-%m-%d %H:%M:%S" > /dev/null
var_time_utc= TZ=UTC date "+%Y-%m-%d %H:%M:%S" > /dev/null
pct set $var_cid \
  --description "This container was last rebuilt on $var_time_utz UTC, $var_time_local local."

# Start CT
echo "Starting container..."
pct start $var_cid

# Provision basics
echo "Provisioning container... "
pct exec $var_cid -- bash -c '
  echo "Fetching updates..." && \
  apt update && \
  echo "Installing updates..." && \
  apt upgrade -y && \
  echo "Installing curl... " && \
  apt install curl -y && \
  echo "Provisioning... Done"  '

echo "Build complete! Please come again."
