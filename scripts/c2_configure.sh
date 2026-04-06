#!/bin/bash
echo "Getting Updates"
apt update && apt upgrade -y

echo "Installing git, curl, and ansible"
apt install git curl ansible -y

echo "Requesting Robot Private Key"
SSHKEYFILE="$home/.ssh/id_robot"
read -ps 'Please paste the vault key: ' SSHKEY
touch "$SSHKEYFILE"
rm "$SSHKEYFILE"
echo "$SSHKEY" >> "$SSHKEYFILE" 
echo
chmod 400 $SSHKEYFILE
echo "Robot Key Setup... Done"

echo "Point Github at the Robot Key"
cat > ~/.ssh/config << EOF
Host github
    HostName github.com
    IdentityFile ~/.ssh/id_robot
EOF


echo "Requesting Vault Secrets"
VAULTKEYFILE="~/secrets/keyfile"
read -ps 'Please paste the vault key: ' VAULTKEY
touch "$VAULTKEYFILE"
rm "$VAULTKEYFILE"
echo "$VAULTKEY" >> "$VAULTKEYFILE" 
echo
chmod 400 $VAULTKEYFILE
echo "Vault Secret Setup... Done"

echo "Cloneing repo"
git clone git@github.com:thataudiobug/frankenas.git
