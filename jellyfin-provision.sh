# Basic updates
apt update && apt upgrade -y

# Adding users
echo "Adding data group..."
groupadd -g 2222 data
usermod -aG data root

# Grab curl, gnupg, and intel stuff
apt install curl gnupg -y
apt install -y intel-opencl-icd

# Install Jellyfin
curl https://repo.jellyfin.org/install-debuntu.sh | SKIP_CONFIRM="true" bash

# Stop jellyfin for perms adjustments
systemctl stop jellyfin.service

# Verify Jellyfin perms
usermod -aG data jellyfin
usermod -aG render jellyfin

systemctl start jellyfin.service
systemctl enable jellyfin.service
