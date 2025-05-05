# get updates and update to 24 LTS #
apt-get update && apt-get upgrade -y
sed -i 's/jammy/noble/g' /etc/apt/sources.list
apt-get update && apt-get upgrade -y
# Install dependancies.
apt install -y dirmngr ca-certificates software-properties-common apt-transport-https
# install Qbit repo and update package lists
add-apt-repository ppa:qbittorrent-team/qbittorrent-stable -y
apt update
# Install Qbit
apt install qbittorrent-nox
# Add Start Service
systemctl daemon-reload
systemctl enable qbittorrent-nox
# Install Radarr
docker run -d \
  --name=radarr \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=Etc/UTC \
  -p 7878:7878 \
  -v /mnt/config/configs/radarr:/config \
  -v /mnt/media/movies:/movies `#optional` \
  -v /mnt/media/downloads:/downloads `#optional` \
  --restart unless-stopped \
  linuxserver/radarr:latest
