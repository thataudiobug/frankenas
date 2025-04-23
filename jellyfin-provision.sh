# get updates and update to 24 LTS #
apt-get update && apt-get upgrade -y
sed -i 's/jammy/noble/g' /etc/apt/sources.list
apt-get update && apt-get upgrade -y
# Grab Curl amd install Jellyfin #
apt install curl -y
curl https://repo.jellyfin.org/install-debuntu.sh | bash
# Pull renderer ID and set perms ie: = render:x:108:jellyfin #
cat /etc/group
usermod -aG render jellyfin
# Install Intel Drivers #
apt install -y intel-opencl-icd

# check current codecs #
/usr/lib/jellyfin-ffmpeg/vainfo --display drm --device /dev/dri/renderD128

