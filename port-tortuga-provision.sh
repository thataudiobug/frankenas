#!/bin/bash
# Get Updates #
apt-get update && apt-get upgrade -y
# Get Qemu-guest-agent #
apt-get install qemu-guest-agent
systemctl start qemu-guest-agent
systemctl enable qemu-guest-agent
# Get PIA #
curl https://installers.privateinternetaccess.com/download/pia-linux-3.7-08412.run
sh pia-linux-3.7-08412.run
# Add Docker's official GPG key: #
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
# Add the repository to Apt sources:
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
# Install docker-ce
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
#install portainer
docker volume create portainer_data
docker run -d -p 8000:8000 -p 9443:9443 --name portainer --restart=always -v /var/run/docker.sock:/var/run/docker.sock -v portainer_data:/data portainer/portainer-ce:lts
# Install Prowlarr
docker run -d \
  --name=prowlarr \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=America/Detroit \
  -p 9696:9696 \
  -v /mnt/config/prowlarr:/config \
  --restart unless-stopped \
  linuxserver/prowlarr:latest
# Install Radarr
docker run -d \
  --name=radarr \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=America/Detroit \
  -p 7878:7878 \
  -v /mnt/config/radarr:/config \
  -v /mnt/media/movies:/movies \
  -v /mnt/media/downloads:/downloads \
  --restart unless-stopped \
  linuxserver/radarr:latest
# Install Sonarr
docker run -d \
  --name=sonarr \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=America/Detroit \
  -p 8989:8989 \
  -v /mnt/config/sonarr:/config \
  -v /mnt/media/tv:/tv \
  -v /mnt/media/downloads:/downloads \
  --restart unless-stopped \
  linuxserver/sonarr:latest
# Install Lidarr
docker run -d \
  --name=lidarr \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=America/Detroit \
  -p 8686:8686 \
  -v /mnt/config/lidarr:/config \
  -v /mnt/media/music:/music \
  -v /mnt/media/downloads:/downloads \
  --restart unless-stopped \
  linuxserver/lidarr:latest
  # Install qbittorrent
docker run -d \
  --name=qbittorrent \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=America/Detroit \
  -e WEBUI_PORT=8080 \
  -e TORRENTING_PORT=6881 \
  -p 6881:6881 \
  -p 6881:6881/udp \
  -p 8080:8080 \
  -v /mnt/config/qbittorrent:/config \
  -v /mnt/media/downloads:/downloads \
  --restart unless-stopped \
  linuxserver/qbittorrent:latest
