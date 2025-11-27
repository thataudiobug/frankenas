# Get updates #
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
