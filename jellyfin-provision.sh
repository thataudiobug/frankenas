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

