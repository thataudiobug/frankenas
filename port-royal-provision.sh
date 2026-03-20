# Get updates #
echo "Fetching updates... "
apt-get update && apt-get upgrade -y

echo "Installing docker GPG key... "
# Add Docker's official GPG key: #
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

# adding users
echo "Adding data group..."
groupadd -g 2222 data
usermod -aG data root

echo "Adding apt repo... "
# Add the repository to Apt sources:
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update

echo "Installing Docker CE... "
# Install docker-ce
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "Installing Portainer... "
#install portainer
docker volume create portainer_data
docker run -d -p 8000:8000 -p 9443:9443 --name portainer --restart=always -v /var/run/docker.sock:/var/run/docker.sock -v portainer_data:/data portainer/portainer-ce:lts

echo "Installing NPM... "
#Install NPM
docker run -d \
  --name=npm \
  -p 80:80 \
  -p 81:81 \
  -p 443:443 \
  -v /mnt/config/npm/data:/data \
  -v /mnt/config/npm/letsencrypt:/letsencrypt \
  --restart unless-stopped \
  jc21/nginx-proxy-manager:latest

echo "Installing Ombi... "
# Install Ombi
docker run -d \
  --name=ombi \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=Etc/UTC \
  -e BASE_URL=/ `#optional` \
  -p 3579:3579 \
  -v /mnt/config/ombi:/config \
  --restart unless-stopped \
  linuxserver/ombi:latest

echo "Installing Nextcloud... "
# Install Nextcloud
docker run -d \
  --name=nextcloud \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=Etc/UTC \
  -p 443:6549 \
  -v /mnt/config:/config \
  -v /mnt/yasha/nextcloud:/data \
  -v /mnt/caleb:/caleb \
  -v /mnt/essek:/essek \
  -v /mnt/nott:/nott \
  -v /mnt/yasha:/yasha \
  --restart unless-stopped \
  lscr.io/linuxserver/nextcloud:latest

echo "provisioning complete!"
