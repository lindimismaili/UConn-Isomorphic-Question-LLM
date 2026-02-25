#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Optional .env override support.
# Example:
#   GITHUB_SSH_KEY_PATH=/home/triforcelindim/.ssh/GitHub_SSH_Key_Raspi
#   GITHUB_SSH_KEY_PASSPHRASE=Lindim123!
#   GIT_FORCE_BRANCH=main
if [[ -f "$REPO_DIR/.env" ]]; then
  # shellcheck disable=SC1091
  set -a
  source "$REPO_DIR/.env"
  set +a
fi

KEY_PATH="${GITHUB_SSH_KEY_PATH:-$HOME/.ssh/GitHub_SSH_Key_Raspi}"
KEY_PASSPHRASE="${GITHUB_SSH_KEY_PASSPHRASE:-Lindim123!}"
TARGET_BRANCH="${GIT_FORCE_BRANCH:-main}"

add_ssh_key_non_interactive() {
  local key_path="$1"
  local key_passphrase="$2"

  [[ -f "$key_path" ]] || {
    echo "ERROR: SSH key not found: $key_path" >&2
    exit 1
  }

  local key_fingerprint
  key_fingerprint="$(ssh-keygen -lf "$key_path" | awk '{print $2}')"
  if ssh-add -l 2>/dev/null | awk '{print $2}' | grep -Fxq "$key_fingerprint"; then
    return
  fi

  local askpass_script
  askpass_script="$(mktemp)"

  cat >"$askpass_script" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "${SSH_KEY_PASSPHRASE:-}"
EOF
  chmod 700 "$askpass_script"

  if ! SSH_KEY_PASSPHRASE="$key_passphrase" \
    DISPLAY="${DISPLAY:-:0}" \
    SSH_ASKPASS="$askpass_script" \
    SSH_ASKPASS_REQUIRE=force \
    setsid -w ssh-add "$key_path" </dev/null >/dev/null 2>&1; then
    rm -f "$askpass_script"
    echo "WARN: Non-interactive ssh-add failed. Falling back to interactive prompt." >&2
    ssh-add "$key_path"
    return
  fi

  rm -f "$askpass_script"
}

ssh-add -l >/dev/null 2>&1
ssh_add_status=$?
if [[ "$ssh_add_status" -eq 2 ]]; then
  eval "$(ssh-agent -s)" >/dev/null
fi

add_ssh_key_non_interactive "$KEY_PATH" "$KEY_PASSPHRASE"

cd "$REPO_DIR"

echo "---- force sync git (${TARGET_BRANCH}) ----"
git fetch origin "$TARGET_BRANCH"
git reset --hard "origin/${TARGET_BRANCH}"
git clean -fd

echo "---- chmod hooks + deploy scripts ----"
chmod +x .githooks/post-merge .githooks/post-rewrite raspi-deploy.sh raspi-config.sh

echo "---- run raspi-config.sh ----"
bash raspi-config.sh
