#!/bin/bash

echo "Getting Updates"
apt update && apt upgrade -y

echo "Installing git, curl, and ansible"
apt install git curl ansible -y

echo "cloneing repo"
git clone git@github.com:thataudiobug/frankenas.git
