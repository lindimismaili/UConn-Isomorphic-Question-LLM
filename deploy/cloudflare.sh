#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# cloudflare.sh — Cloudflare Tunnel setup
#
# Checks if cloudflared is installed and configured. If not, installs it,
# prompts for login, creates the tunnel, sets up DNS routes, and copies
# the repo to /var/www/uconn.
#
# Expects these env vars (set by deploy.sh):
#   DEPLOY_DIR   — /var/www/uconn
#   TUNNEL_NAME  — pi-tunnel
#   HOSTNAME     — uconn.lindimismaili.com
#   REPO_DIR     — path to cloned repo
# =============================================================================

DEPLOY_DIR="${DEPLOY_DIR:-/var/www/uconn}"
TUNNEL_NAME="${TUNNEL_NAME:-pi-tunnel}"
HOSTNAME="${HOSTNAME:-uconn.lindimismaili.com}"
REPO_DIR="${REPO_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

CLOUDFLARED_SYSTEM_DIR="/etc/cloudflared"
CLOUDFLARED_USER_DIR="$HOME/.cloudflared"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info() { echo -e "${GREEN}[cloudflare]${NC} $1"; }
warn() { echo -e "${YELLOW}[cloudflare]${NC} $1"; }
err()  { echo -e "${RED}[cloudflare]${NC} $1" >&2; }

# -------------------------------------------------------------------------
# 1. Install cloudflared if missing
# -------------------------------------------------------------------------
install_cloudflared() {
  if command -v cloudflared >/dev/null 2>&1; then
    info "cloudflared already installed: $(cloudflared --version 2>&1 | head -1)"
    return 0
  fi

  info "Installing cloudflared..."

  # Detect architecture for the correct binary
  local arch
  arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"
  case "$arch" in
    amd64|x86_64)  arch="amd64" ;;
    arm64|aarch64) arch="arm64" ;;
    armhf|armv7l)  arch="arm"   ;;
    *)
      err "Unsupported architecture: $arch"
      exit 1
      ;;
  esac

  # Try apt first (works on Ubuntu/Debian stable)
  local apt_ok=0
  if curl -fsSL --head "https://pkg.cloudflare.com/cloudflared/dists/stable/Release" >/dev/null 2>&1; then
    curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
      | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
    echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared stable main" \
      | sudo tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
    if sudo apt-get update -qq 2>/dev/null && sudo apt-get install -y cloudflared 2>/dev/null; then
      apt_ok=1
    fi
  fi

  # Fallback: download binary directly from GitHub releases
  if [[ "$apt_ok" -eq 0 ]]; then
    info "apt repo unavailable — downloading cloudflared binary directly..."
    # Clean up the broken apt source if it was added
    sudo rm -f /etc/apt/sources.list.d/cloudflared.list

    local url="https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${arch}"
    local tmp_bin="/tmp/cloudflared"

    if ! curl -fsSL -o "$tmp_bin" "$url"; then
      err "Failed to download cloudflared from $url"
      exit 1
    fi

    sudo install -m 755 "$tmp_bin" /usr/local/bin/cloudflared
    rm -f "$tmp_bin"
  fi

  if ! command -v cloudflared >/dev/null 2>&1; then
    err "cloudflared installation failed."
    exit 1
  fi

  info "Installed: $(cloudflared --version 2>&1 | head -1)"
}

# -------------------------------------------------------------------------
# 2. Authenticate with Cloudflare (login)
# -------------------------------------------------------------------------
ensure_authenticated() {
  if [[ -f "$CLOUDFLARED_USER_DIR/cert.pem" ]]; then
    info "Already authenticated (found $CLOUDFLARED_USER_DIR/cert.pem)"
    return 0
  fi

  if [[ -f "$CLOUDFLARED_SYSTEM_DIR/cert.pem" ]]; then
    info "Already authenticated (found $CLOUDFLARED_SYSTEM_DIR/cert.pem)"
    return 0
  fi

  warn "No Cloudflare credentials found. Starting login..."
  echo ""
  echo "  A browser window will open (or a URL will be printed)."
  echo "  Log in to Cloudflare and select your domain."
  echo ""
  cloudflared tunnel login

  if [[ ! -f "$CLOUDFLARED_USER_DIR/cert.pem" ]]; then
    err "Login failed — cert.pem not created."
    exit 1
  fi

  info "Login successful."
}

# -------------------------------------------------------------------------
# 3. Create tunnel if it doesn't exist
# -------------------------------------------------------------------------
ensure_tunnel_exists() {
  local uuid=""

  # Try JSON output first, then fall back to text parsing
  uuid="$(cloudflared tunnel list -o json 2>/dev/null \
    | python3 -c "
import sys, json
tunnels = json.load(sys.stdin)
for t in tunnels:
    if t.get('name') == '${TUNNEL_NAME}' and not t.get('deleted_at'):
        print(t['id']); break
" 2>/dev/null || true)"

  # Fallback: parse text output (columns: ID NAME CREATED CONNECTIONS)
  if [[ -z "$uuid" ]]; then
    uuid="$(cloudflared tunnel list 2>/dev/null \
      | awk -v name="${TUNNEL_NAME}" '$2 == name { print $1; exit }' || true)"
  fi

  # Fallback: check for credentials files matching the tunnel name
  if [[ -z "$uuid" ]]; then
    for f in "$CLOUDFLARED_USER_DIR"/*.json "$CLOUDFLARED_SYSTEM_DIR"/*.json; do
      if [[ -f "$f" ]] && python3 -c "
import json, sys
d = json.load(open('$f'))
if d.get('TunnelName','') == '${TUNNEL_NAME}' or d.get('AccountTag',''):
    sys.exit(0)
sys.exit(1)
" 2>/dev/null; then
        uuid="$(basename "$f" .json)"
        break
      fi
    done
  fi

  if [[ -n "$uuid" ]]; then
    info "Tunnel '$TUNNEL_NAME' already exists (UUID: $uuid)"
  else
    info "Creating tunnel '$TUNNEL_NAME'..."
    # Capture output — cloudflared prints the UUID on creation
    local create_output
    create_output="$(cloudflared tunnel create "$TUNNEL_NAME" 2>&1)" || {
      # If it fails because tunnel already exists, try to extract UUID from error or re-list
      if echo "$create_output" | grep -qi "already exists"; then
        warn "Tunnel already exists — trying to find its UUID..."
        uuid="$(cloudflared tunnel list 2>/dev/null \
          | awk -v name="${TUNNEL_NAME}" '$2 == name { print $1; exit }' || true)"
      else
        err "Failed to create tunnel: $create_output"
        exit 1
      fi
    }

    # Extract UUID from create output if we don't have it yet
    if [[ -z "$uuid" ]]; then
      uuid="$(echo "$create_output" | grep -oP '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' | head -1 || true)"
    fi

    # Last resort — re-list
    if [[ -z "$uuid" ]]; then
      uuid="$(cloudflared tunnel list 2>/dev/null \
        | awk -v name="${TUNNEL_NAME}" '$2 == name { print $1; exit }' || true)"
    fi

    if [[ -z "$uuid" ]]; then
      err "Could not determine tunnel UUID. Run 'cloudflared tunnel list' manually to check."
      exit 1
    fi
    info "Tunnel ready (UUID: $uuid)"
  fi

  # Export for later use
  TUNNEL_UUID="$uuid"
}

# -------------------------------------------------------------------------
# 4. Copy tunnel credentials to system location
# -------------------------------------------------------------------------
setup_system_credentials() {
  sudo mkdir -p "$CLOUDFLARED_SYSTEM_DIR"

  # Copy credentials JSON
  local cred_file=""
  if [[ -f "$CLOUDFLARED_USER_DIR/${TUNNEL_UUID}.json" ]]; then
    cred_file="$CLOUDFLARED_USER_DIR/${TUNNEL_UUID}.json"
  elif [[ -f "$CLOUDFLARED_SYSTEM_DIR/${TUNNEL_UUID}.json" ]]; then
    info "Credentials already in system dir."
    cred_file=""
  else
    err "Cannot find tunnel credentials file for UUID $TUNNEL_UUID"
    err "Looked in: $CLOUDFLARED_USER_DIR/${TUNNEL_UUID}.json"
    exit 1
  fi

  if [[ -n "$cred_file" ]]; then
    sudo cp "$cred_file" "$CLOUDFLARED_SYSTEM_DIR/"
    sudo chown root:root "$CLOUDFLARED_SYSTEM_DIR/${TUNNEL_UUID}.json"
    sudo chmod 600 "$CLOUDFLARED_SYSTEM_DIR/${TUNNEL_UUID}.json"
    info "Copied credentials to $CLOUDFLARED_SYSTEM_DIR/"
  fi

  # Copy cert.pem if not already there
  if [[ ! -f "$CLOUDFLARED_SYSTEM_DIR/cert.pem" ]] && [[ -f "$CLOUDFLARED_USER_DIR/cert.pem" ]]; then
    sudo cp "$CLOUDFLARED_USER_DIR/cert.pem" "$CLOUDFLARED_SYSTEM_DIR/"
    sudo chown root:root "$CLOUDFLARED_SYSTEM_DIR/cert.pem"
    sudo chmod 600 "$CLOUDFLARED_SYSTEM_DIR/cert.pem"
    info "Copied cert.pem to $CLOUDFLARED_SYSTEM_DIR/"
  fi
}

# -------------------------------------------------------------------------
# 5. Write cloudflared config.yml (preserving existing ingress rules)
# -------------------------------------------------------------------------
write_cloudflared_config() {
  local config_file="$CLOUDFLARED_SYSTEM_DIR/config.yml"
  local uconn_rule="  - hostname: $HOSTNAME"

  # Check if config already has our hostname
  if [[ -f "$config_file" ]] && sudo grep -q "$HOSTNAME" "$config_file"; then
    info "cloudflared config already contains $HOSTNAME"
    return 0
  fi

  if [[ -f "$config_file" ]]; then
    # Config exists but doesn't have our hostname — inject our rule before the catch-all
    info "Adding $HOSTNAME to existing cloudflared config..."
    # Insert our ingress rule before the final catch-all "- service: http_status:404"
    sudo python3 -c "
import sys
config_path = '$config_file'
with open(config_path, 'r') as f:
    content = f.read()

# Our ingress block
new_rule = '''  - hostname: $HOSTNAME
    service: http://localhost:80
'''

# Insert before the catch-all
if '- service: http_status:404' in content:
    content = content.replace('  - service: http_status:404', new_rule + '  - service: http_status:404')
else:
    # No catch-all found, append to ingress
    content = content.rstrip() + '\n' + new_rule + '  - service: http_status:404\n'

with open(config_path, 'w') as f:
    f.write(content)
"
    info "Updated $config_file"
  else
    # Create fresh config
    info "Creating new cloudflared config..."
    sudo tee "$config_file" >/dev/null <<EOF
tunnel: ${TUNNEL_NAME}
credentials-file: ${CLOUDFLARED_SYSTEM_DIR}/${TUNNEL_UUID}.json

ingress:
  - hostname: ${HOSTNAME}
    service: http://localhost:80

  - service: http_status:404
EOF
    info "Created $config_file"
  fi

  sudo chmod 644 "$config_file"
}

# -------------------------------------------------------------------------
# 6. Create DNS route for the hostname
# -------------------------------------------------------------------------
setup_dns_route() {
  info "Setting up DNS route: $HOSTNAME → tunnel '$TUNNEL_NAME'..."

  # This is idempotent — cloudflared will report if route already exists
  if cloudflared tunnel route dns "$TUNNEL_NAME" "$HOSTNAME" 2>&1; then
    info "DNS route configured for $HOSTNAME"
  else
    warn "DNS route may already exist (this is OK). Check Cloudflare dashboard if needed."
  fi
}

# -------------------------------------------------------------------------
# 7. Install cloudflared systemd service
# -------------------------------------------------------------------------
install_cloudflared_service() {
  local service_file="/etc/systemd/system/cloudflared.service"

  info "Installing cloudflared systemd service..."

  sudo tee "$service_file" >/dev/null <<'EOF'
[Unit]
Description=cloudflared tunnel
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/bin/cloudflared --no-autoupdate --config /etc/cloudflared/config.yml tunnel run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF

  sudo chmod 644 "$service_file"
  sudo systemctl daemon-reload
  sudo systemctl enable cloudflared

  if systemctl is-active --quiet cloudflared; then
    sudo systemctl restart cloudflared
    info "cloudflared service restarted."
  else
    sudo systemctl start cloudflared
    info "cloudflared service started."
  fi
}

# -------------------------------------------------------------------------
# 8. Rsync the repo to the deploy directory
# -------------------------------------------------------------------------
deploy_repo_to_target() {
  info "Syncing repo to $DEPLOY_DIR..."

  sudo mkdir -p "$DEPLOY_DIR"
  sudo mkdir -p "$DEPLOY_DIR/backend/outputs"
  sudo mkdir -p "$DEPLOY_DIR/backend/uploads"

  sudo rsync -av --delete \
    --exclude ".git/" \
    --exclude ".venv/" \
    --exclude ".env" \
    --exclude "node_modules/" \
    --exclude "frontend/node_modules/" \
    --exclude "frontend/.vite/" \
    --exclude "backend/outputs/" \
    --exclude "backend/uploads/" \
    --exclude "__pycache__/" \
    "${REPO_DIR}/" "${DEPLOY_DIR}/"

  # Preserve ownership for web server
  sudo chown -R www-data:www-data "$DEPLOY_DIR"

  # Copy .env if present in repo and not already in deploy dir
  if [[ -f "$REPO_DIR/.env" ]]; then
    sudo install -m 640 -o www-data -g www-data "$REPO_DIR/.env" "$DEPLOY_DIR/.env"
    info "Copied .env to $DEPLOY_DIR/"
  elif [[ ! -f "$DEPLOY_DIR/.env" ]]; then
    warn "No .env in repo or deploy dir. Create $DEPLOY_DIR/.env with OPENAI_API_KEY."
  fi

  info "Repo synced to $DEPLOY_DIR"
}

# =========================================================================
# MAIN
# =========================================================================
info "Starting Cloudflare tunnel setup..."
echo ""

install_cloudflared
ensure_authenticated
ensure_tunnel_exists
setup_system_credentials
write_cloudflared_config
setup_dns_route
install_cloudflared_service
deploy_repo_to_target

echo ""
info "Cloudflare setup complete."
info "Tunnel: $TUNNEL_NAME (UUID: $TUNNEL_UUID)"
info "Hostname: $HOSTNAME → http://localhost:80"
info "Deploy dir: $DEPLOY_DIR"
