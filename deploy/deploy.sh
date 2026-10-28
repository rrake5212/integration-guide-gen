#!/usr/bin/env bash
# Deploy GuideGen from this laptop to the Lightsail instance.
#
#   deploy/deploy.sh              sync + build + restart + Caddy reload
#   deploy/deploy.sh --bootstrap  also run the one-time server setup first
#   deploy/deploy.sh --no-caddy   app only (skip the vhost install)
#
# Override with env vars:
#   GUIDEGEN_HOST=ubuntu@100.31.120.181
#   GUIDEGEN_SSH_KEY=~/.ssh/LightsailDefaultKey-us-east-1.pem
#   GUIDEGEN_REMOTE_DIR=/home/ubuntu/guidegen
#   GUIDEGEN_DOMAIN=integration-guide.100.31.120.181.sslip.io
set -euo pipefail

HOST="${GUIDEGEN_HOST:-ubuntu@100.31.120.181}"
KEY="${GUIDEGEN_SSH_KEY:-$HOME/.ssh/lightsail-default.pem}"
REMOTE_DIR="${GUIDEGEN_REMOTE_DIR:-/home/ubuntu/guidegen}"
DOMAIN="${GUIDEGEN_DOMAIN:-integration-guide.100.31.120.181.sslip.io}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"

DO_BOOTSTRAP=0
DO_CADDY=1
for arg in "$@"; do
  case "$arg" in
    --bootstrap) DO_BOOTSTRAP=1 ;;
    --no-caddy)  DO_CADDY=0 ;;
    *) echo "unknown flag: $arg" >&2; exit 2 ;;
  esac
done

[ -f "$KEY" ] || { echo "ssh key not found: $KEY (set GUIDEGEN_SSH_KEY)" >&2; exit 1; }
SSH_OPTS=(-i "$KEY" -o StrictHostKeyChecking=accept-new)
remote() { ssh "${SSH_OPTS[@]}" "$HOST" "$@"; }

if [ "$DO_BOOTSTRAP" = 1 ]; then
  echo "== bootstrap (idempotent)"
  ssh "${SSH_OPTS[@]}" "$HOST" "GUIDEGEN_REMOTE_DIR='$REMOTE_DIR' bash -s" < "$SRC/deploy/bootstrap.sh"
fi

echo "== ensure remote dir"
remote "mkdir -p '$REMOTE_DIR'"

echo "== sync source -> $HOST:$REMOTE_DIR"
# .env is excluded in BOTH directions: the server's key material stays on the
# server, and --delete must not remove it. runtime/ holds the SQLite db and job
# artifacts — excluded so a deploy never touches history. data/ IS synced: the
# image bakes in data/golden at build time.
rsync -az --delete \
  --exclude '.venv/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.git/' \
  --exclude '.env' \
  --exclude 'runtime/' \
  -e "ssh -i $KEY -o StrictHostKeyChecking=accept-new" \
  "$SRC/" "$HOST:$REMOTE_DIR/"

echo "== check server .env"
remote "test -f '$REMOTE_DIR/.env'" || {
  cat >&2 <<EOF
missing $REMOTE_DIR/.env on the server.
Create it once from the laptop:
  ssh -i $KEY $HOST "cat > $REMOTE_DIR/.env" < .env && ssh -i $KEY $HOST "chmod 600 $REMOTE_DIR/.env"
EOF
  exit 1
}

echo "== check the server .env holds a usable key"
remote "grep -qE '^DEEPINFRA_API_KEY=.{10,}' '$REMOTE_DIR/.env' && ! grep -q 'your-deepinfra-token' '$REMOTE_DIR/.env'" || {
  echo "server .env has no usable DEEPINFRA_API_KEY (empty, too short, or still the placeholder)." >&2
  echo "Every guide would fail with LLM_NOT_CONFIGURED. Fix the key on the box, then redeploy." >&2
  exit 1
}

echo "== runtime dir owned by the container user (10001)"
remote "sudo install -d -m 755 -o 10001 -g 10001 '$REMOTE_DIR/runtime'"

echo "== build + start container"
remote "cd '$REMOTE_DIR' && sudo docker compose up -d --build --remove-orphans && sudo docker compose ps"

if [ "$DO_CADDY" = 1 ]; then
  echo "== install caddy vhost for $DOMAIN"
  remote "sudo install -d -m 755 /etc/caddy/sites && sudo install -m 644 '$REMOTE_DIR/deploy/Caddyfile' /etc/caddy/sites/guidegen.caddy" || {
    echo "could not install the vhost file" >&2; exit 1
  }
  # bootstrap adds this once; without it Caddy never reads sites/ and the vhost is inert.
  remote "sudo grep -qF 'import /etc/caddy/sites/*.caddy' /etc/caddy/Caddyfile" || {
    echo "/etc/caddy/Caddyfile does not import /etc/caddy/sites/*.caddy — run: deploy/deploy.sh --bootstrap" >&2
    exit 1
  }
  # Validate the WHOLE config (the journey-builder vhost included): a parse error here
  # would take every site on the box down on reload.
  remote "sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile" || {
    echo "caddy rejected the config — nothing was reloaded, the running vhosts are untouched" >&2
    exit 1
  }
  remote "sudo systemctl reload caddy && echo caddy-reloaded"
fi

echo "== local smoke (inside the box)"
remote "bash '$REMOTE_DIR/deploy/smoke.sh' http://127.0.0.1:8765" || {
  echo "smoke failed — logs:" >&2
  remote "cd '$REMOTE_DIR' && sudo docker compose logs --tail=60 app" >&2
  exit 1
}

echo "== public smoke (https)"
# First deploy: Let's Encrypt issuance can lag the reload by a few seconds.
for attempt in 1 2 3 4 5 6; do
  if curl -fsS --max-time 10 "https://$DOMAIN/api/health" >/dev/null 2>&1; then break; fi
  echo "   waiting for TLS/health ($attempt/6)"
  sleep 10
done
bash "$SRC/deploy/smoke.sh" "https://$DOMAIN" || {
  echo "public endpoint not answering — check DNS (sslip.io -> the instance IP) and:" >&2
  echo "  sudo journalctl -u caddy -n 50" >&2
  exit 1
}

echo
echo "deployed: https://$DOMAIN"
