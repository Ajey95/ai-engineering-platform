#!/usr/bin/env bash
set -euo pipefail

# Install the upstream Engine into the existing Ubuntu WSL distro. This leaves
# Docker Desktop's separate images and volumes untouched.
if dpkg-query -W -f='${Status}' docker.io 2>/dev/null | grep -q 'install ok installed'; then
  echo 'Conflicting docker.io package is installed; inspect it before continuing.' >&2
  exit 1
fi

apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc

arch="$(dpkg --print-architecture)"
codename="$(. /etc/os-release && printf '%s' "${UBUNTU_CODENAME:-$VERSION_CODENAME}")"
cat > /etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: ${codename}
Components: stable
Architectures: ${arch}
Signed-By: /etc/apt/keyrings/docker.asc
EOF

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker version
