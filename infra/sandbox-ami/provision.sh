#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo 'Guest image provisioning requires root' >&2
  exit 2
fi
if ! grep -q '^ID=ubuntu$' /etc/os-release || ! grep -q '^VERSION_ID="24.04"$' /etc/os-release; then
  echo 'Guest image requires Ubuntu 24.04' >&2
  exit 2
fi
if [[ $(uname -m) != x86_64 ]]; then
  echo 'Guest image requires x86_64' >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  python3.12 python3.12-venv ca-certificates curl xz-utils tar

NODE_VERSION=24.21.0
NODE_SHA256=fd8e59d5a511510f6a298afb548f18c7d2b1be404d8b4a27d94fbe49f56cb2d6
NODE_TAR="node-v${NODE_VERSION}-linux-x64.tar.xz"
curl --fail --location --silent --show-error \
  "https://nodejs.org/dist/v${NODE_VERSION}/${NODE_TAR}" -o "/tmp/${NODE_TAR}"
printf '%s  %s\n' "${NODE_SHA256}" "/tmp/${NODE_TAR}" | sha256sum --check --status
install -d -m 0755 /opt/node
tar -xJf "/tmp/${NODE_TAR}" --strip-components=1 -C /opt/node
rm -f "/tmp/${NODE_TAR}"

install -d -m 0755 /opt/aip /var/lib/aip
tar -xf /tmp/guest-runtime.tar -C /opt/aip
rm -f /tmp/guest-runtime.tar
python3.12 -m venv /opt/aip/build-venv
/opt/aip/build-venv/bin/pip install --no-cache-dir uv==0.11.3
cd /opt/aip
UV_PROJECT_ENVIRONMENT=/opt/aip/.venv /opt/aip/build-venv/bin/uv sync --frozen --no-dev
PLAYWRIGHT_BROWSERS_PATH=/opt/aip/browsers \
  /opt/aip/.venv/bin/python -m playwright install --with-deps chromium
rm -rf /opt/aip/build-venv

if ! id aipguest >/dev/null 2>&1; then
  useradd --uid 10001 --create-home --shell /usr/sbin/nologin aipguest
fi
chown -R root:root /opt/aip
find /opt/aip -type d -exec chmod 0755 {} +
chmod -R go-w /opt/aip
install -d -o root -g root -m 0755 /var/lib/aip

/opt/aip/.venv/bin/python -c 'import platform_app.sandbox_guest_bootstrap, platform_app.guest_runner'
runuser -u aipguest -- env -i \
  HOME=/home/aipguest PATH=/opt/aip/.venv/bin:/opt/node/bin:/usr/bin:/bin \
  PYTHONPATH=/opt/aip PLAYWRIGHT_BROWSERS_PATH=/opt/aip/browsers \
  /opt/aip/.venv/bin/python -c 'from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); print(b.version); b.close(); p.stop()'
/opt/node/bin/node --version
apt-get clean
rm -rf /var/lib/apt/lists/*
