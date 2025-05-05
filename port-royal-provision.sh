# get updates and update to 24 LTS #
apt-get update && apt-get upgrade -y
sed -i 's/jammy/noble/g' /etc/apt/sources.list
apt-get update && apt-get upgrade -y
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
