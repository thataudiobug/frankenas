#!/bin/bash
echo "Getting Updates"
apt update && apt upgrade -y

echo "Installing git, curl, and ansible"
apt install git curl ansible -y

echo "Cloneing repo"
git clone git@github.com:thataudiobug/frankenas.git

echo "Requesting secrets"
VAULTKEYFILE="$home/secrets/keyfile"
read -ps 'Please paste the vault key: ' SSHKEY
rm "$VAULTKEYFILE"
touch "$VAULTKEYFILE"
echo "$SSHKEY" >> "$VAULTKEYFILE" 
echo
echo "Secrets setup... Done"
