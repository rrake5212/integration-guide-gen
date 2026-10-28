#!/usr/bin/env bash
# One-time setup for GuideGen on the Lightsail instance (Ubuntu 24.04).
#
#   ssh ubuntu@100.31.120.181
#   bash bootstrap.sh          # copy this file over first, or run via deploy.sh --bootstrap
#
# Idempotent: re-running only fills in what is missing. It does NOT touch the
# existing journey-builder-mcp Caddy site block, container, or systemd unit.
set -euo pipefail

say() { printf '== %s\n' "$*"; }

# --- docker engine + compose plugin -----------------------------------------
# rsync runs on BOTH ends of deploy.sh's sync.
if ! command -v rsync >/dev/null 2>&1; then
  say "installing rsync"
  sudo apt-get update -qq && sudo apt-get install -y -qq rsync
fi

if ! command -v docker >/dev/null 2>&1; then
  say "installing docker engine + compose plugin"
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
    | sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu %s stable\n' \
    "$(dpkg --print-architecture)" "$(. /etc/os-release && echo "$VERSION_CODENAME")" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -qq
  sudo apt-get install -y -qq docker-ce docker-ce-cli containerd.io \
    docker-buildx-plugin docker-compose-plugin
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER"
  say "docker installed — run 'newgrp docker' (or reconnect ssh) before using it without sudo"
else
  say "docker present: $(docker --version 2>/dev/null || sudo docker --version)"
fi

if ! sudo docker compose version >/dev/null 2>&1; then
  say "installing compose plugin"
  sudo apt-get install -y -qq docker-compose-plugin
fi

# --- swap: this box is 1 GB and also runs journey-builder-mcp ----------------
# Building a python image can spike past free RAM; swap keeps the build and the
# other service alive instead of OOM-killing them.
if [ "$(swapon --show --noheadings | wc -l)" -eq 0 ] && [ ! -f /swapfile ]; then
  say "adding a 1 GB swapfile (none present)"
  sudo fallocate -l 1G /swapfile 2>/dev/null || sudo dd if=/dev/zero of=/swapfile bs=1M count=1024 status=none
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile >/dev/null
  sudo swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
else
  say "swap already configured"
fi

# --- caddy: let the site file be added without editing the main config ------
if ! command -v caddy >/dev/null 2>&1; then
  say "caddy is not installed; install it first (the existing vhost depends on it)"
  exit 1
fi
sudo install -d -m 755 /etc/caddy/sites
if ! sudo grep -q 'import /etc/caddy/sites/\*.caddy' /etc/caddy/Caddyfile; then
  say "adding 'import /etc/caddy/sites/*.caddy' to /etc/caddy/Caddyfile"
  printf '\nimport /etc/caddy/sites/*.caddy\n' | sudo tee -a /etc/caddy/Caddyfile >/dev/null
  sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
  sudo systemctl reload caddy
fi

# --- app dir ----------------------------------------------------------------
APP_DIR="${GUIDEGEN_REMOTE_DIR:-$HOME/guidegen}"
sudo install -d -m 755 -o 10001 -g 10001 "$APP_DIR/runtime"
say "ready: put .env in $APP_DIR/, then run deploy.sh from the laptop"
