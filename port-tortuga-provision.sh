#!/bin/bash

# Get Updates
apt-get update && apt-get upgrade -y

# Get Curl
if ! command -v curl >/dev/null 2>&1 
then
    echo "Installing Curl"
    apt install curl -y
    echo "Installing Curl... Done"
else
    echo "Curl is installed, moving on."
fi

# Enable ssh
if ! command -v ssh >/dev/null 2>&1 
then
    echo "Installing SSH"
    apt install ssh -y
    echo "Installing SSH... Done"
else
    echo "SSH is installed, moving on."
fi
systemctl start ssh.service
systemctl enable ssh.service

# Get admin pubkey
file="$HOME/.ssh/authorized_keys"
echo "Grabbing newest keys..."
key=$(curl -fsSL "raw.githubusercontent.com/thataudiobug/frankenas/refs/heads/main/pubkey") || {
    echo "Failed to download user SSH keys... shitting the bed"
    exit 1
}
echo "Grabbing newest keys... Done"
touch "$file"
echo "Removing old keys..."
rm "$file"
echo "Removing old keys... Done"
echo "Installing new keys..."
echo "$key" >> "$file"
echo "Installing new keys... Done"

# Get qemu-guest-agent
apt-get install -y qemu-guest-agent 
systemctl start qemu-guest-agent
systemctl enable qemu-guest-agent
echo "Installing qemu-guest-agent... Done"

# Create smb directories
mkdir /mnt/media
mkdir /mnt/config
echo "Creating SMB folders... Done"
# Ask human for SMB user creds
CRED_FILE="/home/.smbcredentials"
read -p 'Please enter the SMB connection Username: ' SMB_USER
read -sp 'Please enter the SMB connection password: ' SMB_PASS
echo "username=$SMB_USER" >> "$CRED_FILE" 
echo "password=$SMB_PASS" >> "$CRED_FILE"
chmod 600 "$CRED_FILE"
echo "SMB credentails save... Done."

# Setup smb connections
echo "//192.168.1.100/essek/media /mnt/media cifs credentials=/home/username/.smbcredentials,uid=1000,gid=1000,file_mode=0775,dir_mode=0775,iocharset=utf8,nounix,noserverino 0 0" | tee -a /etc/fstab > /dev/null
echo "//192.168.1.100/caleb/docker/configs /mnt/configs cifs credentials=/home/username/.smbcredentials,uid=1000,gid=1000,file_mode=0775,dir_mode=0775,iocharset=utf8,nounix,noserverino 0 0" | tee -a /etc/fstab > /dev/null
echo "Adding fstab entries... Done"

# Mount volumes
systemctl daemon-reload
mount -a
echo "Mounting directories... Done"

# Add Docker's official GPG key
apt-get update
apt-get install -y ca-certificates
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "Adding Docker GPG key... Done"

# Add the repository to Apt sources
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "${UBUNTU_CODENAME:-$VERSION_CODENAME}") stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update
echo "Adding Docker Apt source... Done"

# Install Docker-ce
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
echo "Installing Docker... Done"

# Install Portainer
docker volume create portainer_data
docker run -d -p 8000:8000 -p 9443:9443 --name portainer --restart=always -v /var/run/docker.sock:/var/run/docker.sock -v portainer_data:/data portainer/portainer-ce:lts
echo "Installing Portainer... Done"

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
echo "Installing Prowlarr... Done"

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
echo "Installing Radarr... Done"

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
echo "Installing Sonarr... Done"

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
echo "Installing Lidarr... Done"

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
echo "Installing Qbit... Done"

# Installing PIA VPN
curl https://installers.privateinternetaccess.com/download/pia-linux-3.7-08412.run
sh pia-linux-3.7-08412.run
echo "Installing PIA... Done"
