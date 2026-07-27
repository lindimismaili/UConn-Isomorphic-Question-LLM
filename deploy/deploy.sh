#!/usr/bin/env bash
set -euo pipefail

# Install and configure the site on Raspberry Pi OS or Debian-based Linux.

print_usage() {
  cat <<'EOF'
Usage: bash deploy/deploy.sh [command]

No command                         Interactive Raspberry Pi install/update setup
--preflight                        Read-only Pi architecture/resource/tool checks
--status                           Read-only service, update, release, and secret-presence status
--configure-secrets                Set missing OpenAI/browser credentials interactively
--rotate-openai-key                Replace the stored OpenAI API key interactively
--rotate-access-token              Rotate the shared browser access token interactively
--configure-donation               Set or disable the hosted HTTPS PayPal donation URL
--configure-runtime-policy         Edit validated rate, retention, concurrency, and size limits
--rollback <full-commit-sha>       Activate a verified retained release and hold updates off
--recover-maintenance              Verify health/recovery state before clearing maintenance
--update                           Internal root-owned automatic-update service entry point
-h, --help                         Show this help without changing device state

Backup, restore, and interrupted-restore recovery use the separately installed
uconn-isomorphic-backup-restore command; see deploy/BACKUP_RESTORE.md.
EOF
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]] \
  && [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  print_usage
  exit 0
fi

RESTORE_INSTALL_INNER=0
if [[ "${1:-}" == "--restore-install-inner" ]]; then
  RESTORE_INSTALL_INNER=1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
RUNTIME_CONFIG_DIR="/etc/uconn-isomorphic"
DEPLOY_CONFIG_FILE="${RUNTIME_CONFIG_DIR}/deploy.conf"

# Preserve explicit one-shot settings for an interactive installer. Use only
# Bash 3-compatible scalar expansion here so macOS can still reach the
# platform guidance instead of failing while parsing the script.
OVERRIDE_BACKEND_PORT_SET="${BACKEND_PORT+x}"
OVERRIDE_BACKEND_PORT="${BACKEND_PORT-}"
OVERRIDE_DEPLOY_DIR_SET="${DEPLOY_DIR+x}"
OVERRIDE_DEPLOY_DIR="${DEPLOY_DIR-}"
OVERRIDE_PERSIST_DIR_SET="${PERSIST_DIR+x}"
OVERRIDE_PERSIST_DIR="${PERSIST_DIR-}"
OVERRIDE_RELEASE_RETENTION_SET="${RELEASE_RETENTION+x}"
OVERRIDE_RELEASE_RETENTION="${RELEASE_RETENTION-}"
OVERRIDE_CLOUDFLARED_SERVICE_NAME_SET="${CLOUDFLARED_SERVICE_NAME+x}"
OVERRIDE_CLOUDFLARED_SERVICE_NAME="${CLOUDFLARED_SERVICE_NAME-}"
OVERRIDE_CLOUDFLARED_CONFIG_FILE_SET="${CLOUDFLARED_CONFIG_FILE+x}"
OVERRIDE_CLOUDFLARED_CONFIG_FILE="${CLOUDFLARED_CONFIG_FILE-}"

# Deployment settings are data, never shell code. The root-owned installed
# updater reads the same allowlisted file as the interactive installer.
load_deploy_config() {
  local line key value
  [[ -e "${DEPLOY_CONFIG_FILE}" || -L "${DEPLOY_CONFIG_FILE}" ]] || return 0
  if [[ ! -f "${DEPLOY_CONFIG_FILE}" ]] || [[ -L "${DEPLOY_CONFIG_FILE}" ]] \
    || [[ "$(stat -c '%U:%G' "${DEPLOY_CONFIG_FILE}" 2>/dev/null || true)" != "root:root" ]] \
    || [[ -n "$(find "${DEPLOY_CONFIG_FILE}" -maxdepth 0 -perm /022 -print -quit 2>/dev/null)" ]]; then
    printf '[ERROR] Deployment config must be a non-writable root-owned regular file: %s\n' \
      "${DEPLOY_CONFIG_FILE}" >&2
    return 1
  fi
  while IFS= read -r line || [[ -n "${line}" ]]; do
    line="${line%$'\r'}"
    [[ -z "${line}" || "${line}" == \#* ]] && continue
    if [[ ! "${line}" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]]; then
      printf '[ERROR] Invalid deployment setting in %s; expected NAME=value data.\n' \
        "${DEPLOY_CONFIG_FILE}" >&2
      return 1
    fi
    key="${BASH_REMATCH[1]}"
    value="${BASH_REMATCH[2]}"
    if [[ "${#value}" -gt 1024 || "${value}" == *$'\n'* || "${value}" == *$'\r'* ]]; then
      printf '[ERROR] Invalid value for %s in %s.\n' "${key}" "${DEPLOY_CONFIG_FILE}" >&2
      return 1
    fi
    case "${key}" in
      SOURCE_REPO_DIR|LOCAL_HOSTNAME|LOCAL_DOMAIN|DEPLOY_DIR|SERVICE_NAME|BACKEND_PORT|PERSIST_DIR|RELEASE_RETENTION|CLOUDFLARED_SERVICE_NAME|CLOUDFLARED_CONFIG_FILE|AUTO_UPDATE|TUNNEL_NAME|PUBLIC_HOSTNAME|TUNNEL_UUID|UPDATE_REMOTE_URL)
        printf -v "${key}" '%s' "${value}"
        ;;
      *)
        printf '[ERROR] Unsupported deployment setting %s in %s.\n' \
          "${key}" "${DEPLOY_CONFIG_FILE}" >&2
        return 1
        ;;
    esac
  done < "${DEPLOY_CONFIG_FILE}"
}

SOURCE_REPO_DIR="${SOURCE_REPO_DIR:-${DEFAULT_REPO_DIR}}"
DEPLOY_DIR="${DEPLOY_DIR:-/var/www/uconn-isomorphic}"
SERVICE_NAME="${SERVICE_NAME:-uconn-isomorphic}"
BACKEND_PORT="${BACKEND_PORT:-3002}"
PERSIST_DIR="${PERSIST_DIR:-/var/lib/uconn-isomorphic}"
CLOUDFLARED_SERVICE_NAME="${CLOUDFLARED_SERVICE_NAME:-${SERVICE_NAME}-cloudflared}"
CLOUDFLARED_CONFIG_FILE="${CLOUDFLARED_CONFIG_FILE:-/etc/cloudflared/${SERVICE_NAME}.yml}"
RELEASE_RETENTION="${RELEASE_RETENTION:-3}"
load_deploy_config
CONFIGURED_SOURCE_REPO_DIR="${SOURCE_REPO_DIR}"

# A reviewed interactive checkout refreshes its own location. Only the
# immutable installed updater trusts SOURCE_REPO_DIR from root-owned config.
if [[ "${SCRIPT_DIR}" != "/usr/local/libexec/uconn-isomorphic" ]]; then
  SOURCE_REPO_DIR="${DEFAULT_REPO_DIR}"
  [[ -n "${OVERRIDE_BACKEND_PORT_SET}" ]] && BACKEND_PORT="${OVERRIDE_BACKEND_PORT}"
  [[ -n "${OVERRIDE_DEPLOY_DIR_SET}" ]] && DEPLOY_DIR="${OVERRIDE_DEPLOY_DIR}"
  [[ -n "${OVERRIDE_PERSIST_DIR_SET}" ]] && PERSIST_DIR="${OVERRIDE_PERSIST_DIR}"
  [[ -n "${OVERRIDE_RELEASE_RETENTION_SET}" ]] && RELEASE_RETENTION="${OVERRIDE_RELEASE_RETENTION}"
  [[ -n "${OVERRIDE_CLOUDFLARED_SERVICE_NAME_SET}" ]] \
    && CLOUDFLARED_SERVICE_NAME="${OVERRIDE_CLOUDFLARED_SERVICE_NAME}"
  [[ -n "${OVERRIDE_CLOUDFLARED_CONFIG_FILE_SET}" ]] \
    && CLOUDFLARED_CONFIG_FILE="${OVERRIDE_CLOUDFLARED_CONFIG_FILE}"
fi
REPO_DIR="${SOURCE_REPO_DIR}"
CLOUDFLARED_SYSTEM_DIR="/etc/cloudflared"
CLOUDFLARED_USER_DIR="${HOME}/.cloudflared"
CLOUDFLARED_BIN="/usr/bin/cloudflared"
CURL_BIN="/usr/bin/curl"
NODE_LTS_VERSION="22.x"
NODESOURCE_KEY_FINGERPRINT="6F71F525282841EEDAF851B42F59B5F99B1BE0B4"
CLOUDFLARE_KEY_FINGERPRINT="CC94B39C77AE7342A68B89628A682D308D4E5E73"
APT_UPDATED=0
RUNTIME_ENV_FILE="${RUNTIME_CONFIG_DIR}/environment"
RUNTIME_ENV_HELPER_SOURCE="${SCRIPT_DIR}/runtime_env.py"
RUNTIME_ENV_HELPER="/usr/local/libexec/uconn-isomorphic-runtime-env"
BACKUP_RESTORE_HELPER_SOURCE="${SCRIPT_DIR}/backup_restore.py"
BACKUP_RESTORE_HELPER="/usr/local/libexec/uconn-isomorphic-backup-restore"
UPDATER_INSTALL_DIR="/usr/local/libexec/uconn-isomorphic"
UPDATER_SCRIPT="${UPDATER_INSTALL_DIR}/deploy.sh"
APP_USER="uconn-isomorphic"
BUILD_USER="uconn-isomorphic-build"
CLOUDFLARED_USER="uconn-cloudflared"
MIN_FREE_DISK_MIB="${MIN_FREE_DISK_MIB:-8192}"
MIN_RAM_MIB="${MIN_RAM_MIB:-1700}"
RECOMMENDED_RAM_MIB="${RECOMMENDED_RAM_MIB:-3600}"
ONE_TIME_ACCESS_TOKEN=""
MAINTENANCE_ACTIVE=0
DEPLOY_LOCK_FD=""
ACTIVITY_LOCK_FD=""
ACTIVITY_LOCK_INHERITED=0
PENDING_ACTIVATION=0
MIGRATION_STOPPED_SERVICE=0
MAINTENANCE_RECOVERY_FAILED=0
ALLOW_EXISTING_TUNNEL=0
CLOUDFLARED_PROBE_ACTIVE=0
INTEGRITY_REPAIR_ACTIVE=0

# DEPLOY_DIR is the release root, not a mutable working tree. The running app
# is selected through this symlink so a completed candidate can be activated
# atomically and the prior release remains available for rollback.
RELEASES_DIR="${DEPLOY_DIR}/releases"
CURRENT_RELEASE="${DEPLOY_DIR}/current"
PERSIST_RUNTIME_DIR="${PERSIST_DIR}/runtime"
PERSIST_CONFIG_DIR="${PERSIST_DIR}/config"
RUNTIME_CONFIG_FILE="${PERSIST_CONFIG_DIR}/admin-runtime.env"
OPERATIONS_PROBE_FILE="${PERSIST_RUNTIME_DIR}/operations_probe.json"
DEPLOY_STATUS_DIR="/var/lib/${SERVICE_NAME}-deploy"
DEPLOY_STATUS_FILE="${DEPLOY_STATUS_DIR}/deployment_status.json"
DEPLOY_STATUS_LINK="${PERSIST_RUNTIME_DIR}/deployment_status.json"
ACTIVATION_STATE_FILE="${DEPLOY_STATUS_DIR}/pending_activation.json"
INTEGRITY_STATE_FILE="${DEPLOY_STATUS_DIR}/integrity_repair.json"
MANUAL_UPDATE_HOLD_FILE="${DEPLOY_STATUS_DIR}/manual_update_hold.json"
ACTIVATION_RECOVERY_ALLOW_FILE="/run/${SERVICE_NAME}-activation-recovery-allowed"
RESTORE_STATE_FILE="/var/lib/${SERVICE_NAME}-restore.pending"
RESTORE_INSTALL_ALLOW_FILE="/run/${SERVICE_NAME}-restore-install-allowed"
MAINTENANCE_FILE="/run/${SERVICE_NAME}-deployment-maintenance"
DEPLOY_LOCK_DIR="/run/${SERVICE_NAME}-deploy-lock"
DEPLOY_LOCK_FILE="${DEPLOY_LOCK_DIR}/deployment.lock"
ACTIVITY_LOCK_DIR="/run/${SERVICE_NAME}-work-lock"
ACTIVITY_LOCK_FILE="${ACTIVITY_LOCK_DIR}/activity.lock"
ACTIVITY_LOCK_TMPFILES="/etc/tmpfiles.d/${SERVICE_NAME}-work-lock.conf"
DEPLOY_ADMIN_GROUP="sudo"

NGINX_AVAILABLE_DIR="/etc/nginx/sites-available"
NGINX_ENABLED_DIR="/etc/nginx/sites-enabled"
NGINX_CONF="${NGINX_AVAILABLE_DIR}/${SERVICE_NAME}"
NGINX_LINK="${NGINX_ENABLED_DIR}/${SERVICE_NAME}"

DEVICE_HOSTNAME="$(hostname -s 2>/dev/null || hostname 2>/dev/null || true)"
DEVICE_HOSTNAME="${DEVICE_HOSTNAME%%.*}"
if [[ -z "${DEVICE_HOSTNAME}" ]]; then
  DEVICE_HOSTNAME="raspberrypi"
fi
# A replacement Pi derives its restored local-only address from this host,
# just like a normal install.
LOCAL_HOSTNAME="${DEVICE_HOSTNAME}"
LOCAL_DOMAIN="${LOCAL_HOSTNAME}.local"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

banner() { echo -e "\n${CYAN}========================================${NC}\n${CYAN}  $1${NC}\n${CYAN}========================================${NC}\n"; }
info() { echo -e "${GREEN}[INFO]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err() { echo -e "${RED}[ERROR]${NC} $1" >&2; }

systemd_unit_property() {
  local unit="$1" property="$2" value

  if ! value="$(systemctl show "${unit}" --property="${property}" --value 2>/dev/null)"; then
    err "Could not query ${property} for systemd unit ${unit}."
    return 1
  fi
  if [[ -z "${value}" ]]; then
    err "Systemd returned an empty ${property} for unit ${unit}."
    return 1
  fi
  printf '%s\n' "${value}"
}

fsync_directory() {
  sudo python3 - "$1" <<'PY'
import os
import sys

fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
}

atomic_install_root_file() {
  local source="$1" target="$2" mode="$3"

  sudo python3 - "${source}" "${target}" "${mode}" <<'PY'
import os
import stat
import sys
import tempfile
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
mode = int(sys.argv[3], 8)
parent = target.parent

parent_metadata = parent.lstat()
if (
    not stat.S_ISDIR(parent_metadata.st_mode)
    or stat.S_ISLNK(parent_metadata.st_mode)
    or parent_metadata.st_uid != 0
    or stat.S_IMODE(parent_metadata.st_mode) & 0o022
):
    raise SystemExit(f"unsafe root-owned target directory: {parent}")

try:
    target_metadata = target.lstat()
except FileNotFoundError:
    target_metadata = None
if target_metadata is not None and not stat.S_ISREG(target_metadata.st_mode):
    raise SystemExit(f"refusing symlinked or non-regular target: {target}")

source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
try:
    source_metadata = os.fstat(source_fd)
    if not stat.S_ISREG(source_metadata.st_mode):
        raise SystemExit(f"source is not a regular file: {source}")
    payload = bytearray()
    while chunk := os.read(source_fd, 1024 * 1024):
        payload.extend(chunk)
finally:
    os.close(source_fd)

fd, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", dir=parent)
temporary = Path(temporary_name)
try:
    os.fchmod(fd, mode)
    os.fchown(fd, 0, 0)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass

target_fd = os.open(target, os.O_RDONLY | os.O_NOFOLLOW)
try:
    installed = os.fstat(target_fd)
    if (
        not stat.S_ISREG(installed.st_mode)
        or installed.st_uid != 0
        or installed.st_gid != 0
        or stat.S_IMODE(installed.st_mode) != mode
    ):
        raise SystemExit(f"installed target metadata is unsafe: {target}")
    installed_payload = bytearray()
    while chunk := os.read(target_fd, 1024 * 1024):
        installed_payload.extend(chunk)
finally:
    os.close(target_fd)
if installed_payload != payload:
    raise SystemExit(f"installed target content mismatch: {target}")
PY
}

atomic_remove_root_path() {
  local target="$1" expected_parent="$2" expected_type="$3"

  sudo python3 - "${target}" "${expected_parent}" "${expected_type}" <<'PY'
import os
import stat
import sys
from pathlib import Path

target = Path(sys.argv[1])
parent = Path(sys.argv[2])
expected_type = sys.argv[3]
if expected_type not in {"regular", "symlink"}:
    raise SystemExit("unsupported removal type")

parent_metadata = parent.lstat()
if (
    not stat.S_ISDIR(parent_metadata.st_mode)
    or stat.S_ISLNK(parent_metadata.st_mode)
    or parent_metadata.st_uid != 0
    or stat.S_IMODE(parent_metadata.st_mode) & 0o022
):
    raise SystemExit(f"unsafe root-owned target directory: {parent}")
if target.parent != parent:
    raise SystemExit(f"target is not a direct child of {parent}: {target}")

try:
    target_metadata = target.lstat()
except FileNotFoundError:
    target_metadata = None
if target_metadata is not None:
    matches = (
        stat.S_ISREG(target_metadata.st_mode)
        if expected_type == "regular"
        else stat.S_ISLNK(target_metadata.st_mode)
    )
    if not matches:
        raise SystemExit(f"refusing unexpected {expected_type} target type: {target}")
    target.unlink()

directory_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY
}

nginx_enabled_site_target() {
  local site="$1"

  python3 - "${site}" "${NGINX_AVAILABLE_DIR}" "${NGINX_ENABLED_DIR}" <<'PY'
import os
import stat
import sys
from pathlib import Path

site = Path(sys.argv[1])
available = Path(sys.argv[2])
enabled = Path(sys.argv[3])

enabled_metadata = enabled.lstat()
available_metadata = available.lstat()
if (
    not stat.S_ISDIR(enabled_metadata.st_mode)
    or stat.S_ISLNK(enabled_metadata.st_mode)
    or not stat.S_ISDIR(available_metadata.st_mode)
    or stat.S_ISLNK(available_metadata.st_mode)
    or enabled_metadata.st_uid != available_metadata.st_uid
    or enabled_metadata.st_gid != available_metadata.st_gid
    or stat.S_IMODE(enabled_metadata.st_mode) & 0o022
    or stat.S_IMODE(available_metadata.st_mode) & 0o022
    or site.parent != enabled
):
    raise SystemExit(1)
site_metadata = site.lstat()
if not stat.S_ISLNK(site_metadata.st_mode):
    raise SystemExit(1)

raw_target = os.readlink(site)
candidate = Path(raw_target)
if not candidate.is_absolute():
    candidate = site.parent / candidate
try:
    candidate_metadata = candidate.lstat()
    available_resolved = available.resolve(strict=True)
    candidate_resolved = candidate.resolve(strict=True)
except (FileNotFoundError, OSError, RuntimeError):
    raise SystemExit(1)
if (
    not stat.S_ISREG(candidate_metadata.st_mode)
    or stat.S_ISLNK(candidate_metadata.st_mode)
    or candidate_metadata.st_uid != available_metadata.st_uid
    or candidate_metadata.st_gid != available_metadata.st_gid
    or stat.S_IMODE(candidate_metadata.st_mode) & 0o022
    or candidate_resolved.parent != available_resolved
):
    raise SystemExit(1)
print(candidate_resolved)
PY
}

nginx_enabled_site_target_is_safe() {
  nginx_enabled_site_target "$1" >/dev/null
}

atomic_install_nginx_symlink() {
  local target_text="$1" link="$2"

  sudo python3 - \
    "${target_text}" "${link}" "${NGINX_AVAILABLE_DIR}" "${NGINX_ENABLED_DIR}" <<'PY'
import os
import stat
import sys
import tempfile
from pathlib import Path

target_text = sys.argv[1]
link = Path(sys.argv[2])
available = Path(sys.argv[3])
enabled = Path(sys.argv[4])

for directory in (available, enabled):
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) & 0o022
    ):
        raise SystemExit(f"unsafe root-owned nginx directory: {directory}")
if link.parent != enabled:
    raise SystemExit(f"enabled-site link is outside {enabled}: {link}")

candidate = Path(target_text)
if not candidate.is_absolute():
    candidate = link.parent / candidate
candidate_metadata = candidate.lstat()
candidate_resolved = candidate.resolve(strict=True)
available_resolved = available.resolve(strict=True)
if (
    not stat.S_ISREG(candidate_metadata.st_mode)
    or stat.S_ISLNK(candidate_metadata.st_mode)
    or candidate_metadata.st_uid != 0
    or candidate_metadata.st_gid != 0
    or stat.S_IMODE(candidate_metadata.st_mode) & 0o022
    or candidate_resolved.parent != available_resolved
):
    raise SystemExit(f"nginx target escapes sites-available: {target_text}")

try:
    link_metadata = link.lstat()
except FileNotFoundError:
    link_metadata = None
if link_metadata is not None and not stat.S_ISLNK(link_metadata.st_mode):
    raise SystemExit(f"refusing non-symlink enabled-site path: {link}")

fd, temporary_name = tempfile.mkstemp(prefix=f".{link.name}.", dir=enabled)
os.close(fd)
temporary = Path(temporary_name)
temporary.unlink()
try:
    os.symlink(target_text, temporary)
    os.replace(temporary, link)
    directory_fd = os.open(enabled, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass

if not stat.S_ISLNK(link.lstat().st_mode) or link.resolve(strict=True) != candidate_resolved:
    raise SystemExit(f"installed nginx link verification failed: {link}")
PY
}

verify_systemd_unit_install() {
  local unit="$1" expected_path="$2" expected_unit_file_state="${3:-enabled}"
  local fragment_path drop_in_paths load_state unit_file_state need_daemon_reload

  if [[ -L "${expected_path}" ]] || [[ ! -f "${expected_path}" ]] \
    || [[ "$(sudo stat -c '%U:%G:%a' "${expected_path}" 2>/dev/null || true)" != "root:root:644" ]]; then
    err "Installed systemd unit is not a root-owned mode-0644 regular file: ${expected_path}"
    return 1
  fi
  fragment_path="$(systemd_unit_property "${unit}" "FragmentPath")" || return 1
  if [[ "${fragment_path}" != "${expected_path}" ]]; then
    err "Systemd loaded ${unit} from ${fragment_path}, not ${expected_path}."
    return 1
  fi
  if ! drop_in_paths="$(systemctl show "${unit}" --property=DropInPaths --value 2>/dev/null)"; then
    err "Could not inspect systemd drop-ins for ${unit}."
    return 1
  fi
  if [[ -n "${drop_in_paths}" ]]; then
    err "Refusing unexpected systemd drop-ins for ${unit}: ${drop_in_paths}"
    return 1
  fi
  load_state="$(systemd_unit_property "${unit}" "LoadState")" || return 1
  unit_file_state="$(systemd_unit_property "${unit}" "UnitFileState")" || return 1
  need_daemon_reload="$(systemd_unit_property "${unit}" "NeedDaemonReload")" || return 1
  if [[ "${load_state}" != "loaded" ]]; then
    err "Systemd unit ${unit} has LoadState=${load_state}, not loaded."
    return 1
  fi
  if [[ "${unit_file_state}" != "${expected_unit_file_state}" ]]; then
    err "Systemd unit ${unit} has UnitFileState=${unit_file_state}, not ${expected_unit_file_state}."
    return 1
  fi
  if [[ "${need_daemon_reload}" != "no" ]]; then
    err "Systemd unit ${unit} still requires daemon-reload."
    return 1
  fi
}

verify_systemd_unit_state() {
  local unit="$1" expected_load="$2" expected_active="$3" expected_unit_file="$4"
  local load_state active_state unit_file_state

  load_state="$(systemd_unit_property "${unit}" "LoadState")" || return 1
  active_state="$(systemd_unit_property "${unit}" "ActiveState")" || return 1
  unit_file_state="$(systemd_unit_property "${unit}" "UnitFileState")" || return 1
  if [[ "${load_state}" != "${expected_load}" ]] \
    || [[ "${active_state}" != "${expected_active}" ]] \
    || [[ "${unit_file_state}" != "${expected_unit_file}" ]]; then
    err "Systemd unit ${unit} state is ${load_state}/${active_state}/${unit_file_state}; expected ${expected_load}/${expected_active}/${expected_unit_file}."
    return 1
  fi
}

verify_systemd_unit_inactive() {
  local unit="$1" expected_unit_file_state="$2"

  verify_systemd_unit_state \
    "${unit}" "loaded" "inactive" "${expected_unit_file_state}"
}

verify_systemd_unit_inactive_disabled() {
  verify_systemd_unit_state "${1}" "loaded" "inactive" "disabled"
}

disable_systemd_unit_if_present() {
  local unit="$1" load_state active_state unit_file_state

  load_state="$(systemd_unit_property "${unit}" "LoadState")" || return 1
  if [[ "${load_state}" == "not-found" ]]; then
    return 0
  fi
  active_state="$(systemd_unit_property "${unit}" "ActiveState")" || return 1
  unit_file_state="$(systemd_unit_property "${unit}" "UnitFileState")" || return 1
  if [[ "${load_state}" == "masked" ]]; then
    if [[ "${active_state}" != "inactive" || "${unit_file_state}" != "masked" ]]; then
      err "Masked unit ${unit} is not safely inactive/masked."
      return 1
    fi
    return 0
  fi
  if [[ "${load_state}" != "loaded" ]]; then
    err "Systemd unit ${unit} has unsafe LoadState=${load_state}."
    return 1
  fi
  if ! sudo systemctl disable --now "${unit}"; then
    err "Could not disable and stop systemd unit ${unit}."
    return 1
  fi
  sudo systemctl reset-failed "${unit}" >/dev/null 2>&1 || true
  verify_systemd_unit_inactive_disabled "${unit}"
}

stop_systemd_unit_if_present() {
  local unit="$1" load_state active_state unit_file_state

  load_state="$(systemd_unit_property "${unit}" "LoadState")" || return 1
  if [[ "${load_state}" == "not-found" ]]; then
    return 0
  fi
  active_state="$(systemd_unit_property "${unit}" "ActiveState")" || return 1
  unit_file_state="$(systemd_unit_property "${unit}" "UnitFileState")" || return 1
  if [[ "${load_state}" == "masked" ]]; then
    if [[ "${active_state}" != "inactive" || "${unit_file_state}" != "masked" ]]; then
      err "Masked unit ${unit} is not safely inactive/masked."
      return 1
    fi
    return 0
  fi
  if [[ "${load_state}" != "loaded" ]]; then
    err "Systemd unit ${unit} has unsafe LoadState=${load_state}."
    return 1
  fi
  if ! sudo systemctl stop "${unit}"; then
    err "Could not stop systemd unit ${unit}."
    return 1
  fi
  sudo systemctl reset-failed "${unit}" >/dev/null 2>&1 || true
  verify_systemd_unit_inactive "${unit}" "${unit_file_state}"
}

ensure_deploy_status_directory() {
  if sudo test -L "${DEPLOY_STATUS_DIR}" \
    || { sudo test -e "${DEPLOY_STATUS_DIR}" && ! sudo test -d "${DEPLOY_STATUS_DIR}"; }; then
    err "Refusing unsafe deployment status directory: ${DEPLOY_STATUS_DIR}"
    return 1
  fi
  sudo install -d -o root -g root -m 755 "${DEPLOY_STATUS_DIR}"
  if [[ "$(sudo stat -c '%U:%G:%a' "${DEPLOY_STATUS_DIR}" 2>/dev/null || true)" \
    != "root:root:755" ]]; then
    err "Deployment status directory must be root:root mode 0755."
    return 1
  fi
}

write_deployment_maintenance() {
  local reason="$1"
  case "${reason}" in
    activation|cloudflare_setup|deployment|local_only|manual_rollback|ownership_migration|runtime_config)
      ;;
    *)
      err "Refusing unknown deployment-maintenance reason: ${reason}"
      return 1
      ;;
  esac

  sudo env MAINTENANCE_FILE="${MAINTENANCE_FILE}" MAINTENANCE_REASON="${reason}" python3 - <<'PY'
import os
import stat
import tempfile
from pathlib import Path

path = Path(os.environ["MAINTENANCE_FILE"])
parent = path.parent
parent_metadata = parent.lstat()
if (
    not stat.S_ISDIR(parent_metadata.st_mode)
    or stat.S_ISLNK(parent_metadata.st_mode)
    or parent_metadata.st_uid != 0
    or stat.S_IMODE(parent_metadata.st_mode) & 0o022
):
    raise SystemExit("unsafe maintenance parent")
try:
    existing = path.lstat()
except FileNotFoundError:
    existing = None
if existing is not None and not stat.S_ISREG(existing.st_mode):
    raise SystemExit("unsafe maintenance target")

payload = f"schema=1\nreason={os.environ['MAINTENANCE_REASON']}\n".encode()
fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=parent)
temporary = Path(temporary_name)
try:
    os.fchmod(fd, 0o644)
    os.fchown(fd, 0, 0)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
PY
}

read_deployment_maintenance_reason() {
  sudo env MAINTENANCE_FILE="${MAINTENANCE_FILE}" python3 - <<'PY'
import os
import stat
from pathlib import Path

path = Path(os.environ["MAINTENANCE_FILE"])
fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
try:
    metadata = os.fstat(fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o644
    ):
        raise SystemExit(1)
    with os.fdopen(fd, encoding="utf-8") as handle:
        fd = -1
        lines = handle.read(256).splitlines()
finally:
    if fd >= 0:
        os.close(fd)

if len(lines) != 2 or lines[0] != "schema=1" or not lines[1].startswith("reason="):
    raise SystemExit(1)
reason = lines[1].removeprefix("reason=")
if reason not in {
    "activation",
    "cloudflare_setup",
    "deployment",
    "local_only",
    "manual_rollback",
    "ownership_migration",
    "runtime_config",
}:
    raise SystemExit(1)
print(reason)
PY
}

persistent_ownership_marker_is_current() {
  local marker="${PERSIST_DIR}/.ownership-uconn-v1"
  local expected actual
  expected="$(printf 'schema=1\napp_uid=%s\napp_gid=%s' \
    "$(id -u "${APP_USER}")" "$(id -g "${APP_USER}")")"
  if [[ ! -f "${marker}" ]] || [[ -L "${marker}" ]] \
    || [[ "$(sudo stat -c '%U:%G:%a' "${marker}" 2>/dev/null || true)" != "root:root:444" ]]; then
    return 1
  fi
  actual="$(sudo sed -n '1,3p' "${marker}" 2>/dev/null || true)"
  [[ "${actual}" == "${expected}" ]]
}

clear_stale_deployment_maintenance() {
  local reason current current_sha load_state active_state

  if ! sudo test -e "${MAINTENANCE_FILE}" && ! sudo test -L "${MAINTENANCE_FILE}"; then
    return 0
  fi
  if ! reason="$(read_deployment_maintenance_reason)"; then
    err "Deployment maintenance metadata is unsafe or from an older unverified run."
    err "Maintenance was not cleared; inspect the backend, release, and connector state explicitly."
    return 1
  fi
  if sudo test -e "${ACTIVATION_STATE_FILE}" || sudo test -L "${ACTIVATION_STATE_FILE}" \
    || sudo test -e "${INTEGRITY_STATE_FILE}" || sudo test -L "${INTEGRITY_STATE_FILE}" \
    || sudo test -e "${RESTORE_STATE_FILE}" || sudo test -L "${RESTORE_STATE_FILE}"; then
    err "A durable deployment/restore transaction is pending; maintenance was not cleared."
    return 1
  fi
  current="$(current_release_path)"
  current_sha="$(current_release_commit)"
  if [[ -z "${current}" ]] || ! [[ "${current_sha}" =~ ^[0-9a-f]{40}$ ]] \
    || [[ "${current}" != "${RELEASES_DIR}/${current_sha}" ]] \
    || ! release_is_complete "${current}" "${current_sha}"; then
    err "No verified direct release is selected; maintenance was not cleared."
    return 1
  fi
  if [[ "${reason}" == "ownership_migration" ]] && ! persistent_ownership_marker_is_current; then
    err "Persistent ownership migration is incomplete; maintenance was not cleared."
    return 1
  fi
  if [[ "${reason}" == "cloudflare_setup" || "${reason}" == "local_only" ]] \
    && ! cloudflare_connectors_are_quiescent; then
    err "Cloudflare connector state is not proven quiescent; maintenance was not cleared."
    return 1
  fi
  acquire_activity_lock_exclusive || return 1
  load_state="$(systemd_unit_property "${SERVICE_NAME}.service" "LoadState")" || return 1
  if [[ "${load_state}" != "loaded" ]]; then
    err "The backend service is not loaded; maintenance was not cleared."
    return 1
  fi
  active_state="$(systemd_unit_property "${SERVICE_NAME}.service" "ActiveState")" || return 1
  if [[ "${active_state}" != "active" ]]; then
    restart_installed_backend_service || return 1
  fi
  release_health_is_valid "${current_sha}" || return 1
  atomic_remove_root_path "${MAINTENANCE_FILE}" "/run" "regular" || return 1
  MAINTENANCE_ACTIVE=0
  release_activity_lock
}

leave_deployment_maintenance() {
  if [[ "${RESTORE_INSTALL_INNER}" -eq 1 ]]; then
    # The privileged backup/restore parent owns both the marker and the
    # inherited activity-lock file description. It removes/releases them only
    # after exact-commit loopback health verification.
    return 0
  fi
  if [[ "${MAINTENANCE_ACTIVE}" -eq 1 ]]; then
    if sudo test -e "${ACTIVATION_STATE_FILE}" || sudo test -L "${ACTIVATION_STATE_FILE}"; then
      err "Refusing to leave maintenance while an activation transaction is pending."
      return 1
    fi
    if sudo test -e "${INTEGRITY_STATE_FILE}" || sudo test -L "${INTEGRITY_STATE_FILE}"; then
      err "Refusing to leave maintenance while integrity recovery is pending."
      return 1
    fi
    atomic_remove_root_path "${MAINTENANCE_FILE}" "/run" "regular" || return 1
    MAINTENANCE_ACTIVE=0
  fi
  release_activity_lock
}

persist_activation_transaction() {
  local previous_release="$1" previous_sha="$2" candidate_release="$3" candidate_sha="$4" automated="$5"
  ensure_deploy_status_directory || return 1
  sudo env \
    ACTIVATION_STATE_FILE="${ACTIVATION_STATE_FILE}" \
    PREVIOUS_RELEASE="${previous_release}" \
    PREVIOUS_SHA="${previous_sha}" \
    CANDIDATE_RELEASE="${candidate_release}" \
    CANDIDATE_SHA="${candidate_sha}" \
    AUTOMATED="${automated}" \
    python3 - <<'PY'
import json
import os
import tempfile
from pathlib import Path

path = Path(os.environ["ACTIVATION_STATE_FILE"])
payload = {
    "schema": 1,
    "previous_release": os.environ["PREVIOUS_RELEASE"],
    "previous_sha": os.environ["PREVIOUS_SHA"],
    "candidate_release": os.environ["CANDIDATE_RELEASE"],
    "candidate_sha": os.environ["CANDIDATE_SHA"],
    "automated": os.environ["AUTOMATED"] == "true",
}
fd, temporary_name = tempfile.mkstemp(prefix=".pending-activation.", dir=path.parent)
temporary = Path(temporary_name)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    os.chown(path, 0, 0)
    os.chmod(path, 0o600)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
PY
}

read_activation_transaction() {
  sudo env ACTIVATION_STATE_FILE="${ACTIVATION_STATE_FILE}" python3 - <<'PY'
import json
import os
import re
import stat
from pathlib import Path

path = Path(os.environ["ACTIVATION_STATE_FILE"])
parent_metadata = path.parent.lstat()
if (
    not stat.S_ISDIR(parent_metadata.st_mode)
    or stat.S_ISLNK(parent_metadata.st_mode)
    or parent_metadata.st_uid != 0
    or parent_metadata.st_gid != 0
    or stat.S_IMODE(parent_metadata.st_mode) != 0o755
):
    raise SystemExit(1)
fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
try:
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_gid != 0:
        raise SystemExit(1)
    if stat.S_IMODE(metadata.st_mode) != 0o600:
        raise SystemExit(1)
    handle = os.fdopen(fd, encoding="utf-8")
    fd = -1
    with handle:
        payload = json.load(handle)
finally:
    if fd >= 0:
        os.close(fd)

if set(payload) != {
    "schema",
    "previous_release",
    "previous_sha",
    "candidate_release",
    "candidate_sha",
    "automated",
} or payload["schema"] != 1 or not isinstance(payload["automated"], bool):
    raise SystemExit(1)
for key in ("previous_release", "previous_sha", "candidate_release", "candidate_sha"):
    if not isinstance(payload[key], str) or "\n" in payload[key] or "\r" in payload[key]:
        raise SystemExit(1)
if not re.fullmatch(r"[0-9a-f]{40}", payload["candidate_sha"]):
    raise SystemExit(1)
if payload["previous_sha"] and not re.fullmatch(r"[0-9a-f]{40}", payload["previous_sha"]):
    raise SystemExit(1)
print(payload["previous_release"])
print(payload["previous_sha"])
print(payload["candidate_release"])
print(payload["candidate_sha"])
print("true" if payload["automated"] else "false")
PY
}

clear_deployment_state_file() {
  local target="$1" label="$2"

  ensure_deploy_status_directory || return 1
  if ! atomic_remove_root_path "${target}" "${DEPLOY_STATUS_DIR}" "regular"; then
    err "Could not safely clear ${label}: ${target}"
    return 1
  fi
}

clear_activation_transaction() {
  clear_deployment_state_file "${ACTIVATION_STATE_FILE}" "activation state"
}

persist_integrity_repair_state() {
  local temporary

  ensure_deploy_status_directory || return 1
  temporary="$(mktemp)"
  chmod 600 "${temporary}" || {
    rm -f "${temporary}"
    return 1
  }
  if ! printf 'schema=1\nreason=release_integrity\n' > "${temporary}"; then
    rm -f "${temporary}"
    return 1
  fi
  if ! atomic_install_root_file "${temporary}" "${INTEGRITY_STATE_FILE}" 0600; then
    rm -f "${temporary}"
    return 1
  fi
  rm -f "${temporary}"
}

read_integrity_repair_state() {
  sudo env INTEGRITY_STATE_FILE="${INTEGRITY_STATE_FILE}" python3 - <<'PY'
import os
import stat
from pathlib import Path

path = Path(os.environ["INTEGRITY_STATE_FILE"])
parent_metadata = path.parent.lstat()
if (
    not stat.S_ISDIR(parent_metadata.st_mode)
    or stat.S_ISLNK(parent_metadata.st_mode)
    or parent_metadata.st_uid != 0
    or parent_metadata.st_gid != 0
    or stat.S_IMODE(parent_metadata.st_mode) != 0o755
):
    raise SystemExit(1)
fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
try:
    metadata = os.fstat(fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise SystemExit(1)
    with os.fdopen(fd, encoding="utf-8") as handle:
        fd = -1
        content = handle.read(128)
finally:
    if fd >= 0:
        os.close(fd)
if content != "schema=1\nreason=release_integrity\n":
    raise SystemExit(1)
PY
}

clear_integrity_repair_state() {
  clear_deployment_state_file "${INTEGRITY_STATE_FILE}" "integrity-repair state"
}

persist_manual_update_hold() {
  local commit_sha="$1"
  [[ "${commit_sha}" =~ ^[0-9a-f]{40}$ ]] || return 1

  ensure_deploy_status_directory || return 1
  sudo env MANUAL_UPDATE_HOLD_FILE="${MANUAL_UPDATE_HOLD_FILE}" \
    HELD_COMMIT="${commit_sha}" python3 - <<'PY'
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["MANUAL_UPDATE_HOLD_FILE"])
payload = {
    "schema": 1,
    "held_commit": os.environ["HELD_COMMIT"],
    "created_at": datetime.now(timezone.utc)
    .replace(microsecond=0)
    .isoformat()
    .replace("+00:00", "Z"),
}
fd, temporary_name = tempfile.mkstemp(prefix=".manual-update-hold.", dir=path.parent)
temporary = Path(temporary_name)
try:
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chown(temporary, 0, 0)
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
PY
}

read_manual_update_hold() {
  sudo env MANUAL_UPDATE_HOLD_FILE="${MANUAL_UPDATE_HOLD_FILE}" python3 - <<'PY'
import json
import os
import re
import stat
from pathlib import Path

path = Path(os.environ["MANUAL_UPDATE_HOLD_FILE"])
parent_metadata = path.parent.lstat()
if (
    not stat.S_ISDIR(parent_metadata.st_mode)
    or stat.S_ISLNK(parent_metadata.st_mode)
    or parent_metadata.st_uid != 0
    or parent_metadata.st_gid != 0
    or stat.S_IMODE(parent_metadata.st_mode) != 0o755
):
    raise SystemExit(1)
fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
try:
    metadata = os.fstat(fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_gid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise SystemExit(1)
    with os.fdopen(fd, encoding="utf-8") as handle:
        fd = -1
        payload = json.load(handle)
finally:
    if fd >= 0:
        os.close(fd)

if (
    set(payload) != {"schema", "held_commit", "created_at"}
    or payload["schema"] != 1
    or not isinstance(payload["created_at"], str)
    or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z",
        payload["created_at"],
    )
    or not re.fullmatch(r"[0-9a-f]{40}", payload["held_commit"])
):
    raise SystemExit(1)
print(payload["held_commit"])
PY
}

clear_manual_update_hold() {
  clear_deployment_state_file "${MANUAL_UPDATE_HOLD_FILE}" "manual update hold"
}

write_activation_recovery_allow() {
  local temporary

  temporary="$(mktemp)" || return 1
  if ! chmod 600 "${temporary}" \
    || ! atomic_install_root_file \
      "${temporary}" "${ACTIVATION_RECOVERY_ALLOW_FILE}" 0600; then
    rm -f "${temporary}"
    err "Could not safely publish the one-shot activation recovery authorization."
    return 1
  fi
  rm -f "${temporary}"
}

clear_activation_recovery_allow() {
  if ! atomic_remove_root_path \
    "${ACTIVATION_RECOVERY_ALLOW_FILE}" "/run" "regular"; then
    err "Could not safely clear the one-shot activation recovery authorization."
    return 1
  fi
}

recover_pending_activation() {
  local payload previous previous_sha candidate candidate_sha automated current transaction_field
  local current_path current_sha invalid_release workflow_count
  local -a fields=()
  if ! sudo test -e "${ACTIVATION_STATE_FILE}" && ! sudo test -L "${ACTIVATION_STATE_FILE}"; then
    if sudo test -e "${INTEGRITY_STATE_FILE}" \
      || sudo test -L "${INTEGRITY_STATE_FILE}"; then
      write_deployment_maintenance "activation"
      MAINTENANCE_ACTIVE=1
      acquire_activity_lock_exclusive || return 1
      if ! read_integrity_repair_state; then
        err "Integrity-repair metadata is unsafe or corrupt; keeping maintenance enabled."
        stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
        return 1
      fi
      INTEGRITY_REPAIR_ACTIVE=1
    fi
    return 0
  fi

  write_deployment_maintenance "activation"
  MAINTENANCE_ACTIVE=1
  acquire_activity_lock_exclusive || return 1
  if ! payload="$(read_activation_transaction)"; then
    err "Activation recovery metadata is unsafe or corrupt; keeping maintenance mode enabled."
    stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
    return 1
  fi
  while IFS= read -r transaction_field; do
    fields[${#fields[@]}]="${transaction_field}"
  done <<< "${payload}"
  if [[ "${#fields[@]}" -ne 5 ]]; then
    err "Activation recovery metadata is incomplete; keeping maintenance mode enabled."
    stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
    return 1
  fi
  previous="${fields[0]}"
  previous_sha="${fields[1]}"
  candidate="${fields[2]}"
  candidate_sha="${fields[3]}"
  automated="${fields[4]}"

  if [[ "${candidate}" != "${RELEASES_DIR}/${candidate_sha}" ]]; then
    err "Pending candidate path is invalid; keeping maintenance mode enabled."
    stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
    return 1
  fi
  if [[ -n "${previous}" ]]; then
    if release_is_verified_direct "${previous}" "${previous_sha}"; then
      warn "Recovering interrupted activation by restoring ${previous_sha}."
      switch_current_release "${previous}" || return 1
      current="${previous_sha}"
    elif release_is_verified_direct "${candidate}" "${candidate_sha}" \
      && [[ "$(current_release_path)" == "${candidate}" ]]; then
      warn "The recorded rollback release is invalid; retaining the verified selected candidate."
      current="${candidate_sha}"
    else
      err "Recorded rollback release no longer passes integrity validation; keeping maintenance mode enabled."
      stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
      return 1
    fi
  else
    if release_is_verified_direct "${candidate}" "${candidate_sha}"; then
      warn "No prior release exists; validating the first candidate before accepting it."
      switch_current_release "${candidate}" || return 1
      current="${candidate_sha}"
    else
      # Integrity repair persists this transaction before moving a corrupt
      # direct release. Resume that exact quarantine after a power loss, or
      # clear a post-quarantine transaction so the updater can rebuild while
      # maintenance remains fail-closed.
      current_path="$(current_release_path)"
      current_sha="$(current_release_commit)"
      if [[ -n "${current_path}" ]] \
        && [[ "${current_sha}" =~ ^[0-9a-f]{40}$ ]] \
        && [[ "${current_path}" == "${RELEASES_DIR}/${current_sha}" ]] \
        && [[ -d "${current_path}" ]] && [[ ! -L "${current_path}" ]] \
        && ! release_is_complete "${current_path}" "${current_sha}"; then
        workflow_count="$(active_workflow_count)"
        if (( workflow_count != 0 )); then
          err "Interrupted integrity repair cannot quarantine a release while workflow state is ${workflow_count}."
          return 1
        fi
        persist_integrity_repair_state || return 1
        stop_systemd_unit_if_present "${SERVICE_NAME}.service" || return 1
        invalid_release="${RELEASES_DIR}/.invalid-${current_sha}-$(date -u +%Y%m%d%H%M%S)"
        sudo mv -- "${current_path}" "${invalid_release}" || return 1
        atomic_remove_root_path "${CURRENT_RELEASE}" "${DEPLOY_DIR}" "symlink" || return 1
        fsync_directory "${RELEASES_DIR}" || return 1
      elif [[ -L "${CURRENT_RELEASE}" ]] && [[ ! -e "${CURRENT_RELEASE}" ]]; then
        atomic_remove_root_path "${CURRENT_RELEASE}" "${DEPLOY_DIR}" "symlink" || return 1
      elif [[ -e "${CURRENT_RELEASE}" || -L "${CURRENT_RELEASE}" ]]; then
        err "Pending first-release recovery found an unsafe current selector; keeping maintenance enabled."
        stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
        return 1
      fi
      if ! sudo test -e "${INTEGRITY_STATE_FILE}" \
        && ! sudo test -L "${INTEGRITY_STATE_FILE}"; then
        persist_integrity_repair_state || return 1
      fi
      stop_systemd_unit_if_present "${SERVICE_NAME}.service" || return 1
      clear_activation_transaction || return 1
      PENDING_ACTIVATION=0
      INTEGRITY_REPAIR_ACTIVE=1
      warn "Interrupted integrity repair requires a clean candidate rebuild; maintenance remains enabled."
      return 0
    fi
  fi

  sudo install -o root -g root -m 600 /dev/null "${ACTIVATION_RECOVERY_ALLOW_FILE}"
  if activate_backend_service "${automated}" && release_health_is_valid "${current}"; then
    sudo rm -f "${ACTIVATION_RECOVERY_ALLOW_FILE}"
    clear_activation_transaction
    PENDING_ACTIVATION=0
    if sudo test -e "${INTEGRITY_STATE_FILE}" \
      || sudo test -L "${INTEGRITY_STATE_FILE}"; then
      clear_integrity_repair_state || return 1
      INTEGRITY_REPAIR_ACTIVE=0
    fi
    MIGRATION_STOPPED_SERVICE=0
    MAINTENANCE_RECOVERY_FAILED=0
    leave_deployment_maintenance
    return 0
  fi

  sudo rm -f "${ACTIVATION_RECOVERY_ALLOW_FILE}"
  stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
  err "Activation recovery could not verify a healthy release; the backend is stopped and maintenance remains enabled."
  return 1
}

cleanup_deployment_transaction() {
  if [[ "${CLOUDFLARED_PROBE_ACTIVE}" -eq 1 ]]; then
    if ! disable_systemd_unit_if_present "${CLOUDFLARED_SERVICE_NAME}.service"; then
      MAINTENANCE_RECOVERY_FAILED=1
      err "Could not stop the unverified Cloudflare probe service; maintenance remains enabled."
      return 0
    fi
    CLOUDFLARED_PROBE_ACTIVE=0
  fi
  if [[ "${MAINTENANCE_RECOVERY_FAILED}" -eq 1 ]]; then
    return 0
  fi
  if [[ "${PENDING_ACTIVATION}" -eq 1 ]] \
    || sudo test -e "${ACTIVATION_STATE_FILE}" \
    || sudo test -L "${ACTIVATION_STATE_FILE}"; then
    if ! recover_pending_activation; then
      MAINTENANCE_RECOVERY_FAILED=1
      return 0
    fi
  elif sudo test -e "${INTEGRITY_STATE_FILE}" \
    || sudo test -L "${INTEGRITY_STATE_FILE}"; then
    MAINTENANCE_RECOVERY_FAILED=1
    err "Integrity repair is still pending; maintenance remains enabled."
    return 0
  elif [[ "${MIGRATION_STOPPED_SERVICE}" -eq 1 ]]; then
    warn "Deployment exited after an ownership migration; restarting the prior backend."
    if restart_installed_backend_service; then
      MIGRATION_STOPPED_SERVICE=0
    else
      MAINTENANCE_RECOVERY_FAILED=1
      sudo systemctl stop "${SERVICE_NAME}" >/dev/null 2>&1 || true
      err "The prior backend did not recover; maintenance mode remains enabled."
      return 0
    fi
  fi
  leave_deployment_maintenance || true
}

enter_deployment_maintenance() {
  local reason="$1"
  write_deployment_maintenance "${reason}" || return 1
  MAINTENANCE_ACTIVE=1
  trap cleanup_deployment_transaction EXIT
  trap 'cleanup_deployment_transaction; exit 130' INT
  trap 'cleanup_deployment_transaction; exit 143' TERM
  acquire_activity_lock_exclusive || return 1
}

acquire_deployment_lock() {
  if [[ -n "${DEPLOY_LOCK_FD}" ]]; then
    return 0
  fi
  if sudo test -L "${DEPLOY_LOCK_DIR}" \
    || { sudo test -e "${DEPLOY_LOCK_DIR}" && ! sudo test -d "${DEPLOY_LOCK_DIR}"; }; then
    err "Refusing unsafe deployment lock directory: ${DEPLOY_LOCK_DIR}"
    return 1
  fi
  if ! getent group "${DEPLOY_ADMIN_GROUP}" >/dev/null; then
    err "Required deployment administrator group is missing: ${DEPLOY_ADMIN_GROUP}"
    return 1
  fi
  sudo install -d -o root -g "${DEPLOY_ADMIN_GROUP}" -m 750 "${DEPLOY_LOCK_DIR}"
  if [[ "$(sudo stat -c '%U:%G:%a' "${DEPLOY_LOCK_DIR}" 2>/dev/null || true)" != "root:${DEPLOY_ADMIN_GROUP}:750" ]]; then
    err "Deployment lock directory must be root:${DEPLOY_ADMIN_GROUP} mode 750."
    return 1
  fi
  if sudo test -L "${DEPLOY_LOCK_FILE}" \
    || { sudo test -e "${DEPLOY_LOCK_FILE}" && ! sudo test -f "${DEPLOY_LOCK_FILE}"; }; then
    err "Refusing unsafe deployment lock file: ${DEPLOY_LOCK_FILE}"
    return 1
  fi
  if ! sudo test -e "${DEPLOY_LOCK_FILE}"; then
    sudo install -o root -g "${DEPLOY_ADMIN_GROUP}" -m 440 /dev/null "${DEPLOY_LOCK_FILE}"
  fi
  if [[ "$(sudo stat -c '%U:%G:%a' "${DEPLOY_LOCK_FILE}" 2>/dev/null || true)" != "root:${DEPLOY_ADMIN_GROUP}:440" ]]; then
    err "Deployment lock file must be root:${DEPLOY_ADMIN_GROUP} mode 440."
    return 1
  fi
  exec {DEPLOY_LOCK_FD}<"${DEPLOY_LOCK_FILE}"
  flock -n "${DEPLOY_LOCK_FD}"
}

verify_activity_lock() {
  if sudo test -L "${ACTIVITY_LOCK_DIR}" \
    || ! sudo test -d "${ACTIVITY_LOCK_DIR}" \
    || [[ "$(sudo stat -c '%U:%G:%a' "${ACTIVITY_LOCK_DIR}" 2>/dev/null || true)" \
      != "root:root:755" ]]; then
    err "Paid-work activity lock directory must be a real root:root mode 0755 directory."
    return 1
  fi
  if sudo test -L "${ACTIVITY_LOCK_FILE}" \
    || ! sudo test -f "${ACTIVITY_LOCK_FILE}" \
    || [[ "$(sudo stat -c '%U:%G:%a:%h' "${ACTIVITY_LOCK_FILE}" 2>/dev/null || true)" \
      != "root:root:444:1" ]]; then
    err "Paid-work activity lock must be one root:root mode 0444 regular inode."
    return 1
  fi
}

ensure_activity_lock() {
  local tmpfiles_source

  if sudo test -L "${ACTIVITY_LOCK_DIR}" \
    || { sudo test -e "${ACTIVITY_LOCK_DIR}" && ! sudo test -d "${ACTIVITY_LOCK_DIR}"; }; then
    err "Refusing unsafe paid-work activity lock directory: ${ACTIVITY_LOCK_DIR}"
    return 1
  fi
  if ! sudo test -e "${ACTIVITY_LOCK_DIR}"; then
    sudo install -d -o root -g root -m 755 "${ACTIVITY_LOCK_DIR}" || return 1
  fi
  if [[ "$(sudo stat -c '%U:%G:%a' "${ACTIVITY_LOCK_DIR}" 2>/dev/null || true)" \
    != "root:root:755" ]]; then
    err "Refusing to alter unsafe paid-work activity lock directory metadata."
    return 1
  fi
  if sudo test -L "${ACTIVITY_LOCK_FILE}" \
    || { sudo test -e "${ACTIVITY_LOCK_FILE}" && ! sudo test -f "${ACTIVITY_LOCK_FILE}"; }; then
    err "Refusing unsafe paid-work activity lock file: ${ACTIVITY_LOCK_FILE}"
    return 1
  fi
  if ! sudo test -e "${ACTIVITY_LOCK_FILE}"; then
    sudo install -o root -g root -m 444 /dev/null "${ACTIVITY_LOCK_FILE}" || return 1
  fi
  verify_activity_lock || return 1

  tmpfiles_source="$(mktemp)"
  chmod 600 "${tmpfiles_source}"
  if ! printf 'd %s 0755 root root -\nf %s 0444 root root -\n' \
      "${ACTIVITY_LOCK_DIR}" "${ACTIVITY_LOCK_FILE}" > "${tmpfiles_source}"; then
    rm -f "${tmpfiles_source}"
    return 1
  fi
  if ! sudo install -d -o root -g root -m 755 "$(dirname "${ACTIVITY_LOCK_TMPFILES}")" \
    || ! atomic_install_root_file "${tmpfiles_source}" "${ACTIVITY_LOCK_TMPFILES}" 0644; then
    rm -f "${tmpfiles_source}"
    return 1
  fi
  rm -f "${tmpfiles_source}"
  sudo systemd-tmpfiles --create "${ACTIVITY_LOCK_TMPFILES}" || return 1
  verify_activity_lock
}

acquire_activity_lock_exclusive() {
  local descriptor_identity path_identity

  if [[ -n "${ACTIVITY_LOCK_FD}" ]]; then
    return 0
  fi
  ACTIVITY_LOCK_INHERITED=0
  ensure_activity_lock || return 1
  if ! exec {ACTIVITY_LOCK_FD}<"${ACTIVITY_LOCK_FILE}"; then
    ACTIVITY_LOCK_FD=""
    err "Could not open the paid-work activity lock."
    return 1
  fi
  if ! flock -x "${ACTIVITY_LOCK_FD}"; then
    exec {ACTIVITY_LOCK_FD}>&-
    ACTIVITY_LOCK_FD=""
    err "Could not acquire the paid-work activity lock."
    return 1
  fi
  if ! verify_activity_lock; then
    exec {ACTIVITY_LOCK_FD}>&-
    ACTIVITY_LOCK_FD=""
    return 1
  fi
  descriptor_identity="$(
    stat -Lc '%d:%i' "/proc/$$/fd/${ACTIVITY_LOCK_FD}" 2>/dev/null || true
  )"
  path_identity="$(stat -Lc '%d:%i' "${ACTIVITY_LOCK_FILE}" 2>/dev/null || true)"
  if [[ -z "${descriptor_identity}" || "${descriptor_identity}" != "${path_identity}" ]]; then
    exec {ACTIVITY_LOCK_FD}>&-
    ACTIVITY_LOCK_FD=""
    err "Paid-work activity lock inode changed during acquisition."
    return 1
  fi
}

release_activity_lock() {
  if [[ -z "${ACTIVITY_LOCK_FD}" ]]; then
    return 0
  fi
  if [[ "${ACTIVITY_LOCK_INHERITED}" -ne 1 ]]; then
    flock -u "${ACTIVITY_LOCK_FD}" || true
  fi
  exec {ACTIVITY_LOCK_FD}>&-
  ACTIVITY_LOCK_FD=""
  ACTIVITY_LOCK_INHERITED=0
}

ask_to_continue() {
  local prompt="$1"

  if [[ ! -t 0 ]]; then
    return 1
  fi

  printf "%s (Enter to continue, any other key to close): " "${prompt}"
  local reply=""
  IFS= read -r -n 1 reply || true
  echo ""
  [[ -z "${reply}" ]]
}

is_explicit_yes() {
  local reply="$1"
  reply="$(trim_value "${reply}")"
  reply="$(printf '%s' "${reply}" | tr '[:upper:]' '[:lower:]')"
  [[ "${reply}" == "y" || "${reply}" == "yes" ]]
}

confirm_default_no() {
  local prompt="$1"
  local reply=""

  if [[ ! -t 0 ]]; then
    return 1
  fi

  read -rp "${prompt} [y/N]: " reply || return 1
  is_explicit_yes "${reply}"
}

prompt_with_default() {
  local prompt="$1"
  local default_value="$2"
  local value=""

  read -rp "${prompt} [${default_value}]: " value
  value="$(trim_value "${value}")"
  printf '%s\n' "${value:-$default_value}"
}

trim_value() {
  local value="$1"

  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  printf '%s\n' "${value}"
}

is_valid_port() {
  [[ "$1" =~ ^[0-9]+$ ]] && (( $1 >= 1 && $1 <= 65535 ))
}

is_valid_service_name() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9_.@-]*$ ]]
}

trusted_root_is_safe() {
  local path="$1"
  [[ ! -e "${path}" ]] && return 0
  [[ -d "${path}" ]] && [[ ! -L "${path}" ]] \
    && [[ "$(stat -c '%U:%G' "${path}" 2>/dev/null || true)" == "root:root" ]] \
    && [[ -z "$(find "${path}" -maxdepth 0 -perm /022 -print -quit 2>/dev/null)" ]]
}

is_valid_short_hostname() {
  [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]]
}

normalize_hostname_input() {
  local hostname="$1"

  hostname="${hostname#http://}"
  hostname="${hostname#https://}"
  hostname="${hostname%%/*}"
  hostname="${hostname%%:*}"
  hostname="${hostname%.}"
  printf '%s\n' "${hostname}" | tr '[:upper:]' '[:lower:]'
}

is_valid_dns_hostname() {
  local hostname="$1"
  local label
  local labels=()

  [[ -n "${hostname}" ]] || return 1
  [[ "${hostname}" != *" "* ]] || return 1
  [[ "${hostname}" == *.* ]] || return 1
  [[ "${#hostname}" -le 253 ]] || return 1

  IFS='.' read -ra labels <<< "${hostname}"
  for label in "${labels[@]}"; do
    [[ "${label}" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?$ ]] || return 1
  done
}

is_valid_update_remote_url() {
  local value="$1"
  [[ "${value}" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(\.git)?$ ]]
}

validate_deploy_settings() {
  local canonical_deploy canonical_persist canonical_cloudflare canonical_repo

  if ! is_valid_port "${BACKEND_PORT}"; then
    err "BACKEND_PORT must be an integer from 1 to 65535; got ${BACKEND_PORT}."
    exit 1
  fi

  if ! is_valid_service_name "${SERVICE_NAME}"; then
    err "SERVICE_NAME contains unsafe characters: ${SERVICE_NAME}"
    exit 1
  fi
  if [[ ! "${SERVICE_NAME}" =~ ^uconn-[a-z0-9][a-z0-9-]{0,47}$ ]] \
    || [[ "${SERVICE_NAME}" == *-update ]] \
    || [[ "${SERVICE_NAME}" == *-cloudflared ]]; then
    err "SERVICE_NAME must use the dedicated uconn-* namespace and must not use a managed-unit suffix."
    exit 1
  fi

  if ! is_valid_service_name "${CLOUDFLARED_SERVICE_NAME}"; then
    err "CLOUDFLARED_SERVICE_NAME contains unsafe characters: ${CLOUDFLARED_SERVICE_NAME}"
    exit 1
  fi
  if [[ "${CLOUDFLARED_SERVICE_NAME}" != "${SERVICE_NAME}-cloudflared" ]]; then
    err "CLOUDFLARED_SERVICE_NAME must be ${SERVICE_NAME}-cloudflared to avoid managed-unit collisions."
    exit 1
  fi

  if ! is_valid_short_hostname "${LOCAL_HOSTNAME}"; then
    err "Device hostname is not safe for local DNS/server_name: ${LOCAL_HOSTNAME}"
    exit 1
  fi

  canonical_deploy="$(realpath -m -- "${DEPLOY_DIR}" 2>/dev/null || true)"
  canonical_persist="$(realpath -m -- "${PERSIST_DIR}" 2>/dev/null || true)"
  canonical_cloudflare="$(realpath -m -- "${CLOUDFLARED_CONFIG_FILE}" 2>/dev/null || true)"
  canonical_repo="$(realpath -m -- "${REPO_DIR}" 2>/dev/null || true)"

  if [[ "${DEPLOY_DIR}" != /* ]] || [[ "${canonical_deploy}" != "${DEPLOY_DIR%/}" ]]; then
    err "DEPLOY_DIR must be a canonical absolute path; got ${DEPLOY_DIR}."
    exit 1
  fi

  if [[ "${DEPLOY_DIR}" == *[[:space:]]* ]] \
    || [[ ! "${DEPLOY_DIR}" =~ ^/var/www/[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    err "DEPLOY_DIR must be a dedicated path directly beneath /var/www without whitespace."
    exit 1
  fi

  if [[ "${PERSIST_DIR}" != /* ]] || [[ "${canonical_persist}" != "${PERSIST_DIR%/}" ]] \
    || [[ "${PERSIST_DIR}" == *[[:space:]]* ]] \
    || [[ ! "${PERSIST_DIR}" =~ ^/var/lib/[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    err "PERSIST_DIR must be a dedicated canonical path directly beneath /var/lib."
    exit 1
  fi
  if [[ "${PERSIST_DIR}" == "${DEPLOY_STATUS_DIR}" ]] || [[ "${PERSIST_DIR}" == "${DEPLOY_DIR}" ]] \
    || [[ "${PERSIST_DIR}" == "${DEPLOY_DIR}/"* ]] || [[ "${DEPLOY_DIR}" == "${PERSIST_DIR}/"* ]]; then
    err "Deployment, persistent-data, and root status paths must remain separate."
    exit 1
  fi
  if ! trusted_root_is_safe /var || ! trusted_root_is_safe /var/lib \
    || ! trusted_root_is_safe /var/www; then
    err "Trusted deployment roots must be real root-owned directories without group/other write access."
    exit 1
  fi

  if ! [[ "${RELEASE_RETENTION}" =~ ^[0-9]+$ ]]; then
    err "RELEASE_RETENTION must be a non-negative integer; got ${RELEASE_RETENTION}."
    exit 1
  fi

  if ! [[ "${MIN_FREE_DISK_MIB}" =~ ^[0-9]+$ ]] || (( MIN_FREE_DISK_MIB < 1 )); then
    err "MIN_FREE_DISK_MIB must be a positive integer; got ${MIN_FREE_DISK_MIB}."
    exit 1
  fi

  if ! [[ "${MIN_RAM_MIB}" =~ ^[0-9]+$ ]] || (( MIN_RAM_MIB < 1 )); then
    err "MIN_RAM_MIB must be a positive integer; got ${MIN_RAM_MIB}."
    exit 1
  fi

  if ! [[ "${RECOMMENDED_RAM_MIB}" =~ ^[0-9]+$ ]] || (( RECOMMENDED_RAM_MIB < MIN_RAM_MIB )); then
    err "RECOMMENDED_RAM_MIB must be an integer greater than or equal to MIN_RAM_MIB."
    exit 1
  fi

  if [[ "${canonical_cloudflare}" != "${CLOUDFLARED_CONFIG_FILE}" ]] \
    || [[ "$(dirname "${CLOUDFLARED_CONFIG_FILE}")" != "${CLOUDFLARED_SYSTEM_DIR}" ]] \
    || [[ "${CLOUDFLARED_CONFIG_FILE}" == *[[:space:]]* ]] \
    || [[ ! "$(basename "${CLOUDFLARED_CONFIG_FILE}")" =~ ^[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
    err "CLOUDFLARED_CONFIG_FILE must be a canonical path directly beneath ${CLOUDFLARED_SYSTEM_DIR}."
    exit 1
  fi

  if [[ "${REPO_DIR}" != /* ]] || [[ "${canonical_repo}" != "${REPO_DIR%/}" ]] \
    || [[ "${REPO_DIR}" == *[[:space:]]* ]]; then
    err "The source checkout must use a canonical absolute path without whitespace."
    exit 1
  fi

  if [[ "${canonical_repo}" == "${canonical_deploy}" ]] \
    || [[ "${canonical_repo}" == "${canonical_deploy}/"* ]] \
    || [[ "${canonical_deploy}" == "${canonical_repo}/"* ]] \
    || [[ "${canonical_repo}" == "${canonical_persist}" ]] \
    || [[ "${canonical_repo}" == "${canonical_persist}/"* ]] \
    || [[ "${canonical_persist}" == "${canonical_repo}/"* ]]; then
    err "The source checkout, deployment root, and persistent-data root must not overlap."
    exit 1
  fi

  if [[ -n "${PUBLIC_HOSTNAME:-}" ]]; then
    PUBLIC_HOSTNAME="$(normalize_hostname_input "${PUBLIC_HOSTNAME}")"
    if ! is_valid_dns_hostname "${PUBLIC_HOSTNAME}"; then
      err "PUBLIC_HOSTNAME must be a DNS hostname without a path or port; got ${PUBLIC_HOSTNAME}."
      exit 1
    fi
  fi

  if [[ -n "${TUNNEL_UUID:-}" ]] && ! is_tunnel_uuid "${TUNNEL_UUID}"; then
    warn "Ignoring invalid saved TUNNEL_UUID=${TUNNEL_UUID}."
    unset TUNNEL_UUID
  fi

  if [[ -n "${TUNNEL_UUID:-}" ]] && ! tunnel_credential_exists "${TUNNEL_UUID}"; then
    warn "Ignoring tunnel UUID ${TUNNEL_UUID}; no local credential JSON exists for it."
    unset TUNNEL_UUID
  fi

  if [[ -n "${UPDATE_REMOTE_URL:-}" ]] && ! is_valid_update_remote_url "${UPDATE_REMOTE_URL}"; then
    err "UPDATE_REMOTE_URL must be a pinned GitHub HTTPS repository URL."
    exit 1
  fi
}

select_platform() {
  echo "What system are you running this script on?"
  echo "1) Mac"
  echo "2) Windows"
  echo "3) Raspberry Pi / Linux"
  echo ""

  local platform_choice=""
  if ! read -rp "Choose 1, 2, or 3: " platform_choice; then
    err "Platform selection is required."
    exit 1
  fi

  case "${platform_choice}" in
    1)
      warn "The full deploy installer uses apt, nginx, systemd, and avahi, so it is only for Raspberry Pi / Linux."
      info "For local Mac preview, run: bash deploy/preview.sh --dev"
      exit 0
      ;;
    2)
      warn "The full deploy installer uses apt, nginx, systemd, and avahi, so it is only for Raspberry Pi / Linux."
      info "For Windows, use WSL/Linux and choose option 3, or run the app manually from your normal dev environment."
      exit 0
      ;;
    3)
      return
      ;;
    *)
      err "Invalid choice. Run again and choose 1, 2, or 3."
      exit 1
      ;;
  esac
}

require_linux() {
  if [[ "$(uname -s)" != "Linux" ]]; then
    err "This installer is intended for Raspberry Pi OS or another Debian-based Linux system."
    err "Detected: $(uname -s)"
    exit 1
  fi
}

require_repo() {
  if [[ ! -f "${REPO_DIR}/site/backend/requirements.txt" ]] \
    || [[ ! -f "${REPO_DIR}/site/backend/src/server.py" ]] \
    || [[ ! -f "${REPO_DIR}/site/frontend/package.json" ]] \
    || [[ ! -f "${REPO_DIR}/site/frontend/package-lock.json" ]] \
    || [[ ! -f "${RUNTIME_ENV_HELPER_SOURCE}" ]] \
    || [[ ! -f "${BACKUP_RESTORE_HELPER_SOURCE}" ]]; then
    err "Cannot find expected repo files under ${REPO_DIR}."
    err "Run this from the cloned UConn-IsomorphicQuestionLLM repository."
    exit 1
  fi
}

nearest_existing_parent() {
  local path="$1"
  while [[ ! -e "${path}" ]]; do
    if [[ "${path}" == "/" ]]; then
      break
    fi
    path="$(dirname "${path}")"
  done
  printf '%s\n' "${path}"
}

available_disk_mib() {
  local path existing available_kib
  path="$1"
  existing="$(nearest_existing_parent "${path}")"
  available_kib="$(df -Pk -- "${existing}" 2>/dev/null | awk 'NR == 2 {print $4}')"
  [[ "${available_kib}" =~ ^[0-9]+$ ]] || return 1
  printf '%s\n' "$((available_kib / 1024))"
}

architecture_is_supported() {
  local machine_arch="$1" package_arch="$2"
  [[ "${machine_arch}" == "aarch64" && "${package_arch}" == "arm64" ]]
}

run_pi_preflight() {
  banner "Raspberry Pi Preflight"

  local machine_arch package_arch python_version ram_mib deploy_disk_mib persist_disk_mib
  machine_arch="$(uname -m 2>/dev/null || true)"
  package_arch="$(dpkg --print-architecture 2>/dev/null || true)"
  if ! architecture_is_supported "${machine_arch}" "${package_arch}"; then
    err "This deployment supports 64-bit Raspberry Pi OS only."
    err "Detected kernel architecture '${machine_arch:-unknown}' and package architecture '${package_arch:-unknown}'."
    err "Install the 64-bit Raspberry Pi OS image (aarch64/arm64) before deploying."
    return 1
  fi

  if ! command -v python3 >/dev/null 2>&1 \
    || ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    err "Python 3.10 or newer is required before the installer can make changes."
    err "Install a current 64-bit Raspberry Pi OS release and rerun this script."
    return 1
  fi
  python_version="$(python3 -c 'import platform; print(platform.python_version())')"

  ram_mib="$(awk '/^MemTotal:/ && $2 ~ /^[0-9]+$/ {print int($2 / 1024); exit}' /proc/meminfo 2>/dev/null || true)"
  if ! [[ "${ram_mib}" =~ ^[0-9]+$ ]]; then
    err "Could not determine installed memory from /proc/meminfo."
    return 1
  fi
  if (( ram_mib < MIN_RAM_MIB )); then
    err "Only ${ram_mib} MiB RAM was detected; a 2 GB-class Raspberry Pi is the minimum supported."
    return 1
  fi
  if (( ram_mib < RECOMMENDED_RAM_MIB )); then
    warn "${ram_mib} MiB RAM detected. A 4 GB or larger Pi is recommended for reliable builds."
  fi

  deploy_disk_mib="$(available_disk_mib "${DEPLOY_DIR}")" || {
    err "Could not determine free disk space for ${DEPLOY_DIR}."
    return 1
  }
  persist_disk_mib="$(available_disk_mib "${PERSIST_DIR}")" || {
    err "Could not determine free disk space for ${PERSIST_DIR}."
    return 1
  }
  if (( deploy_disk_mib < MIN_FREE_DISK_MIB )); then
    err "${DEPLOY_DIR} has ${deploy_disk_mib} MiB free; at least ${MIN_FREE_DISK_MIB} MiB is required."
    return 1
  fi
  if (( persist_disk_mib < MIN_FREE_DISK_MIB )); then
    err "${PERSIST_DIR} has ${persist_disk_mib} MiB free; at least ${MIN_FREE_DISK_MIB} MiB is required."
    return 1
  fi

  info "Architecture: aarch64/arm64"
  info "Python: ${python_version}"
  info "Memory: ${ram_mib} MiB"
  info "Free disk: deploy ${deploy_disk_mib} MiB; persistent data ${persist_disk_mib} MiB"
}

require_sudo_access() {
  if ! command -v sudo >/dev/null 2>&1; then
    err "sudo is required for package installation, nginx, systemd, and Cloudflare service setup."
    exit 1
  fi

  sudo -v
}

require_sudo() {
  require_sudo_access
  sync_etc_hosts
}

ensure_locked_system_user() {
  local user_name="$1" user_shell user_home user_uid primary_group password_state group_count
  if ! getent passwd "${user_name}" >/dev/null; then
    sudo useradd --system --user-group --home-dir /nonexistent --shell /usr/sbin/nologin "${user_name}"
    return 0
  fi
  user_shell="$(getent passwd "${user_name}" | cut -d: -f7)"
  user_home="$(getent passwd "${user_name}" | cut -d: -f6)"
  user_uid="$(id -u "${user_name}")"
  primary_group="$(id -gn "${user_name}")"
  password_state="$(sudo passwd -S "${user_name}" 2>/dev/null | awk '{print $2}')"
  group_count="$(id -Gn "${user_name}" | awk '{print NF}')"
  if [[ "${user_shell}" != "/usr/sbin/nologin" && "${user_shell}" != "/bin/false" ]]; then
    err "Refusing existing login-capable service account ${user_name} (${user_shell})."
    return 1
  fi
  if (( user_uid >= 1000 )) || [[ "${user_home}" != "/nonexistent" ]] \
    || [[ "${primary_group}" != "${user_name}" ]] || [[ "${password_state}" != "L" ]] \
    || [[ "${group_count}" != "1" ]]; then
    err "Refusing preexisting ${user_name}; it is not a dedicated locked, unprivileged system identity."
    return 1
  fi
}

ensure_service_users() {
  ensure_locked_system_user "${APP_USER}"
  ensure_locked_system_user "${BUILD_USER}"
}

install_runtime_env_helper() {
  if [[ ! -f "${RUNTIME_ENV_HELPER_SOURCE}" ]]; then
    if [[ -x "${RUNTIME_ENV_HELPER}" ]]; then
      return 0
    fi
    err "Missing root runtime-environment helper source: ${RUNTIME_ENV_HELPER_SOURCE}"
    return 1
  fi
  sudo install -d -o root -g root -m 755 "$(dirname "${RUNTIME_ENV_HELPER}")"
  sudo install -o root -g root -m 755 "${RUNTIME_ENV_HELPER_SOURCE}" "${RUNTIME_ENV_HELPER}"
}

install_backup_restore_helper() {
  local active_release active_sha helper_source

  active_release="$(current_release_path)"
  active_sha="$(current_release_commit)"
  if ! [[ "${active_sha}" =~ ^[0-9a-f]{40}$ ]] \
    || [[ "${active_release}" != "${RELEASES_DIR}/${active_sha}" ]] \
    || ! release_is_complete "${active_release}" "${active_sha}"; then
    err "Backup/restore helper installation requires a verified active release."
    return 1
  fi
  helper_source="${active_release}/deploy/backup_restore.py"
  if [[ ! -f "${helper_source}" || -L "${helper_source}" ]] \
    || [[ "$(stat -c '%U:%G' "${helper_source}" 2>/dev/null || true)" != "root:root" ]]; then
    err "Verified release does not contain a safe backup/restore helper."
    return 1
  fi
  sudo install -d -o root -g root -m 755 "$(dirname "${BACKUP_RESTORE_HELPER}")"
  atomic_install_root_file "${helper_source}" "${BACKUP_RESTORE_HELPER}" 0755 || return 1
  if [[ ! -f "${BACKUP_RESTORE_HELPER}" || -L "${BACKUP_RESTORE_HELPER}" ]] \
    || [[ "$(stat -c '%U:%G:%a' "${BACKUP_RESTORE_HELPER}" 2>/dev/null || true)" \
      != "root:root:755" ]] \
    || ! sudo cmp -s -- "${helper_source}" "${BACKUP_RESTORE_HELPER}"; then
    err "Installed backup/restore helper failed its ownership or content check."
    return 1
  fi
}

ensure_runtime_environment_file() {
  sudo install -d -o root -g root -m 755 "${RUNTIME_CONFIG_DIR}"
  if sudo test -L "${RUNTIME_ENV_FILE}"; then
    err "Refusing symlinked runtime environment: ${RUNTIME_ENV_FILE}"
    return 1
  fi
  if sudo test -e "${RUNTIME_ENV_FILE}" && ! sudo test -f "${RUNTIME_ENV_FILE}"; then
    err "Runtime environment is not a regular file: ${RUNTIME_ENV_FILE}"
    return 1
  fi
  if ! sudo test -e "${RUNTIME_ENV_FILE}"; then
    sudo install -o root -g root -m 600 /dev/null "${RUNTIME_ENV_FILE}"
  else
    sudo chown root:root "${RUNTIME_ENV_FILE}"
    sudo chmod 600 "${RUNTIME_ENV_FILE}"
  fi
}

validate_runtime_secret_installation() {
  [[ -x "${RUNTIME_ENV_HELPER}" ]] || {
    err "Installed runtime environment helper is missing."
    return 1
  }
  if ! sudo test -f "${RUNTIME_ENV_FILE}" || sudo test -L "${RUNTIME_ENV_FILE}"; then
    err "Persistent runtime environment is missing or unsafe."
    return 1
  fi
  [[ "$(sudo stat -c '%U:%G:%a' "${RUNTIME_ENV_FILE}" 2>/dev/null || true)" == "root:root:600" ]] || {
    err "Persistent runtime environment must be root:root mode 600."
    return 1
  }
  if ! sudo "${RUNTIME_ENV_HELPER}" validate "${RUNTIME_ENV_FILE}"; then
    err "Persistent runtime environment contains unsupported, duplicate, or invalid settings."
    return 1
  fi
}

runtime_env_has_value() {
  local key="$1"
  sudo "${RUNTIME_ENV_HELPER}" has "${RUNTIME_ENV_FILE}" "${key}"
}

write_runtime_secret() {
  local key="$1"
  sudo "${RUNTIME_ENV_HELPER}" set "${RUNTIME_ENV_FILE}" "${key}"
}

clear_runtime_setting() {
  local key="$1"
  sudo "${RUNTIME_ENV_HELPER}" unset "${RUNTIME_ENV_FILE}" "${key}"
}

prompt_for_openai_key() {
  local force="${1:-false}"
  local secret="" confirmation="" restore_xtrace=0

  if [[ "${force}" != "true" ]] && runtime_env_has_value "OPENAI_API_KEY"; then
    return 0
  fi
  if [[ ! -t 0 ]]; then
    err "OPENAI_API_KEY is missing from ${RUNTIME_ENV_FILE}."
    err "Run interactively: bash deploy/deploy.sh --configure-secrets"
    return 1
  fi

  [[ "$-" == *x* ]] && restore_xtrace=1 && set +x
  while true; do
    if ! IFS= read -rsp "OpenAI API key (input hidden): " secret; then
      printf '\n'
      unset secret confirmation
      (( restore_xtrace == 1 )) && set -x
      err "OpenAI API key entry was cancelled."
      return 1
    fi
    printf '\n'
    if ! IFS= read -rsp "Confirm OpenAI API key: " confirmation; then
      printf '\n'
      unset secret confirmation
      (( restore_xtrace == 1 )) && set -x
      err "OpenAI API key confirmation was cancelled."
      return 1
    fi
    printf '\n'
    if [[ -z "${secret}" ]]; then
      warn "The key cannot be empty."
    elif [[ "${secret}" != "${confirmation}" ]]; then
      warn "The two entries did not match."
    elif [[ ! "${secret}" =~ ^[A-Za-z0-9._~+/=-]+$ ]]; then
      warn "The key contains unsupported characters or whitespace."
    else
      printf '%s' "${secret}" | write_runtime_secret "OPENAI_API_KEY"
      unset secret confirmation
      (( restore_xtrace == 1 )) && set -x
      info "OpenAI API key stored in the root-owned device environment."
      return 0
    fi
    unset secret confirmation
  done
}

generate_access_token() {
  local force="${1:-false}"
  local token="" restore_xtrace=0

  if [[ "${force}" != "true" ]] && runtime_env_has_value "APP_ACCESS_TOKEN"; then
    return 0
  fi

  [[ "$-" == *x* ]] && restore_xtrace=1 && set +x
  token="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  printf '%s' "${token}" | write_runtime_secret "APP_ACCESS_TOKEN"
  ONE_TIME_ACCESS_TOKEN="${token}"
  unset token
  (( restore_xtrace == 1 )) && set -x
  info "A browser access token was generated and stored in the device environment."
}

ensure_runtime_secrets() {
  install_runtime_env_helper
  ensure_runtime_environment_file
  prompt_for_openai_key false
  generate_access_token false
}

show_one_time_access_token() {
  local restore_xtrace=0
  [[ "$-" == *x* ]] && restore_xtrace=1 && set +x

  if [[ -z "${ONE_TIME_ACCESS_TOKEN}" ]]; then
    (( restore_xtrace == 1 )) && set -x
    return 0
  fi
  if [[ -t 1 ]]; then
    warn "Copy the browser access token below now. It will not be shown again."
    warn "Paste it into the site's access prompt; do not send it by email or commit it."
    printf '\n%s\n\n' "${ONE_TIME_ACCESS_TOKEN}"
  else
    warn "A browser access token was generated but not printed because output is not an interactive terminal."
    warn "Rotate it interactively with: bash deploy/deploy.sh --rotate-access-token"
  fi
  unset ONE_TIME_ACCESS_TOKEN
  (( restore_xtrace == 1 )) && set -x
}

apt_update_once() {
  if [[ "${APT_UPDATED}" -eq 0 ]]; then
    info "Updating apt package list..."
    sudo apt-get update
    APT_UPDATED=1
  fi
}

apt_install_if_missing() {
  local package_name="$1"
  local command_name="${2:-$1}"

  if command -v "${command_name}" >/dev/null 2>&1; then
    return
  fi

  apt_update_once
  info "Installing ${package_name}..."
  sudo apt-get install -y "${package_name}"
}

apt_install_package_if_missing() {
  local package_name="$1"
  if dpkg-query -W -f='${db:Status-Status}' "${package_name}" 2>/dev/null | grep -qx installed; then
    return 0
  fi
  apt_update_once
  info "Installing ${package_name}..."
  sudo apt-get install -y "${package_name}"
}

verify_gpg_primary_fingerprint() {
  local key_file="$1" expected="$2" label="$3" actual
  actual="$(
    gpg --batch --show-keys --with-colons "${key_file}" 2>/dev/null \
      | awk -F: '$1 == "fpr" { print toupper($10); exit }'
  )"
  if [[ "${actual}" != "${expected}" ]]; then
    err "${label} signing-key fingerprint mismatch."
    err "Expected ${expected}; received ${actual:-unreadable}. No apt trust was changed."
    return 1
  fi
}

node_version_is_supported() {
  command -v node >/dev/null 2>&1 || return 1

  local version major minor patch
  version="$(node --version 2>/dev/null || true)"
  version="${version#v}"
  IFS='.' read -r major minor patch <<< "${version}"
  patch="${patch%%[^0-9]*}"

  [[ "${major}" =~ ^[0-9]+$ ]] || return 1
  [[ "${minor}" =~ ^[0-9]+$ ]] || minor=0
  [[ "${patch}" =~ ^[0-9]+$ ]] || patch=0

  if (( major >= 24 )); then return 0; fi
  if (( major == 22 && minor >= 13 )); then return 0; fi
  if (( major == 20 && minor >= 19 )); then return 0; fi
  return 1
}

npm_version_is_supported() {
  local version major
  command -v npm >/dev/null 2>&1 || return 1
  version="$(npm --version 2>/dev/null || true)"
  major="${version%%.*}"
  [[ "${major}" =~ ^[0-9]+$ ]] && (( major >= 10 ))
}

install_node_from_nodesource() {
  apt_install_if_missing curl
  apt_install_if_missing ca-certificates update-ca-certificates
  apt_install_if_missing gnupg gpg

  local key_file keyring="/usr/share/keyrings/nodesource.gpg"
  key_file="$(mktemp)"
  info "Configuring NodeSource's signed Node.js ${NODE_LTS_VERSION} apt repository..."
  curl -fsSL "https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key" -o "${key_file}"
  verify_gpg_primary_fingerprint \
    "${key_file}" "${NODESOURCE_KEY_FINGERPRINT}" "NodeSource"
  gpg --batch --yes --dearmor --output "${key_file}.gpg" "${key_file}"
  sudo install -o root -g root -m 644 "${key_file}.gpg" "${keyring}"
  rm -f "${key_file}" "${key_file}.gpg"
  printf 'deb [signed-by=%s] https://deb.nodesource.com/node_22.x nodistro main\n' "${keyring}" \
    | sudo tee /etc/apt/sources.list.d/nodesource.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y nodejs
  hash -r
}

ensure_node() {
  if node_version_is_supported && npm_version_is_supported; then
    info "Node.js $(node --version) and npm $(npm --version) are installed."
    return
  fi

  if command -v node >/dev/null 2>&1; then
    warn "Node.js $(node --version) is missing npm or is too old for Vite. Installing Node.js ${NODE_LTS_VERSION}."
  else
    warn "Node.js is not installed. Installing Node.js ${NODE_LTS_VERSION}."
  fi

  install_node_from_nodesource

  if ! node_version_is_supported || ! npm_version_is_supported; then
    err "Node.js/npm installation did not produce a Vite-compatible Node runtime."
    err "Need Node 20.19+, Node 22.13+, or Node 24+ with npm 10+."
    exit 1
  fi

  info "Node.js $(node --version) and npm $(npm --version) are ready."
}

install_system_packages() {
  banner "System Packages"

  apt_install_if_missing git
  apt_install_if_missing rsync
  apt_install_if_missing nginx
  apt_install_if_missing curl
  apt_install_if_missing ca-certificates update-ca-certificates
  apt_install_if_missing iproute2 ss
  apt_install_if_missing avahi-daemon avahi-daemon
  apt_install_if_missing python3
  apt_install_package_if_missing python3-venv
  apt_install_if_missing python3-pip pip3
  apt_install_if_missing procps pgrep
  ensure_node

  sudo systemctl enable --now avahi-daemon

  info "Python: $(python3 --version 2>&1)"
  info "Node:   $(node --version 2>&1)"
  info "npm:    $(npm --version 2>&1)"
}

configure_local_domain() {
  banner "Local Address"

  info "Using existing device hostname ${LOCAL_HOSTNAME}."
  info "Local site address will be http://${LOCAL_DOMAIN}"
}

sync_etc_hosts() {
  # Keep /etc/hosts in step with the current hostname. Without this, sudo can
  # print "unable to resolve host <name>" when the hostname and hosts file drift.
  local hosts_line="127.0.1.1	${LOCAL_HOSTNAME} ${LOCAL_DOMAIN}"

  if grep -qE "^127\.0\.1\.1[[:space:]]+${LOCAL_HOSTNAME}([[:space:]]+${LOCAL_DOMAIN})?([[:space:]]|$)" /etc/hosts; then
    info "/etc/hosts already maps 127.0.1.1 to ${LOCAL_HOSTNAME}."
  elif grep -qE '^127\.0\.1\.1[[:space:]]' /etc/hosts; then
    info "Updating the 127.0.1.1 entry in /etc/hosts to ${LOCAL_HOSTNAME}."
    sudo sed -i -E "s/^127\.0\.1\.1[[:space:]].*/${hosts_line}/" /etc/hosts
  else
    info "Adding ${LOCAL_HOSTNAME} to /etc/hosts."
    printf '%s\n' "${hosts_line}" | sudo tee -a /etc/hosts >/dev/null
  fi
}

warn_if_openai_key_missing() {
  if ! runtime_env_has_value "OPENAI_API_KEY"; then
    warn "OPENAI_API_KEY is missing from the persistent device environment."
    warn "Configure it without exposing the value: bash deploy/deploy.sh --configure-secrets"
  fi
}

backend_port_pids() {
  if command -v ss >/dev/null 2>&1; then
    sudo ss -H -ltnp "sport = :${BACKEND_PORT}" 2>/dev/null \
      | sed -nE 's/.*pid=([0-9]+).*/\1/p' \
      | sort -u
  elif command -v lsof >/dev/null 2>&1; then
    sudo lsof -tiTCP:"${BACKEND_PORT}" -sTCP:LISTEN 2>/dev/null | sort -u
  fi
}

pid_is_this_backend() {
  local pid="$1"
  local args

  args="$(ps -p "${pid}" -o args= 2>/dev/null || true)"
  [[ "${args}" == *"backend.src.server:app"* ]] || [[ "${args}" == *"${DEPLOY_DIR}/.venv/bin/uvicorn"* ]]
}

stop_existing_backend_service() {
  local load_state

  load_state="$(systemd_unit_property "${SERVICE_NAME}.service" "LoadState")" || return 1
  if [[ "${load_state}" != "not-found" ]]; then
    info "Stopping any existing ${SERVICE_NAME} service before restart."
    stop_systemd_unit_if_present "${SERVICE_NAME}.service"
  fi
}

ensure_backend_port_available() {
  local pids pid

  mapfile -t pids < <(backend_port_pids)
  if [[ "${#pids[@]}" -eq 0 ]]; then
    return
  fi

  for pid in "${pids[@]}"; do
    if [[ -z "${pid}" ]]; then
      continue
    fi

    if pid_is_this_backend "${pid}"; then
      warn "Stopping stale backend process ${pid} on port ${BACKEND_PORT}."
      sudo kill "${pid}" 2>/dev/null || true
    else
      err "Port ${BACKEND_PORT} is already in use by a non-deploy process:"
      sudo ss -ltnp "sport = :${BACKEND_PORT}" || true
      err "Stop that process or rerun with BACKEND_PORT=<free-port> bash deploy/deploy.sh."
      return 1
    fi
  done

  sleep 1
  mapfile -t pids < <(backend_port_pids)
  if [[ "${#pids[@]}" -eq 0 ]]; then
    return
  fi

  for pid in "${pids[@]}"; do
    if [[ -n "${pid}" ]] && pid_is_this_backend "${pid}"; then
      warn "Force-stopping stale backend process ${pid} on port ${BACKEND_PORT}."
      sudo kill -9 "${pid}" 2>/dev/null || true
    fi
  done

  sleep 1
  mapfile -t pids < <(backend_port_pids)
  if [[ "${#pids[@]}" -gt 0 ]]; then
    err "Port ${BACKEND_PORT} is still busy:"
    sudo ss -ltnp "sport = :${BACKEND_PORT}" || true
    return 1
  fi
}

wait_for_backend_health() {
  local remaining=60

  while (( remaining > 0 )); do
    if "${CURL_BIN}" -q --noproxy '*' --proto '=http' \
      --connect-timeout 2 --max-time 5 -fsS \
      "http://127.0.0.1:${BACKEND_PORT}/api/health" >/dev/null; then
      return 0
    fi

    if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
      err "${SERVICE_NAME} stopped before it became healthy."
      err "Check: sudo journalctl -u ${SERVICE_NAME} -n 50"
      return 1
    fi

    sleep 1
    remaining=$((remaining - 1))
  done

  err "${SERVICE_NAME} did not pass health check on port ${BACKEND_PORT} within 60 seconds."
  err "Check: sudo journalctl -u ${SERVICE_NAME} -n 50"
  return 1
}

install_backend_service() {
  banner "Backend Service"

  local service_file="/etc/systemd/system/${SERVICE_NAME}.service"
  local service_source
  local allowed_origins="http://${LOCAL_DOMAIN},http://${LOCAL_HOSTNAME},http://localhost,http://127.0.0.1,http://127.0.0.1:8080"
  if [[ -n "${PUBLIC_HOSTNAME:-}" ]]; then
    allowed_origins="${allowed_origins},https://${PUBLIC_HOSTNAME}"
  fi

  service_source="$(mktemp)"
  chmod 600 "${service_source}" || {
    rm -f "${service_source}"
    return 1
  }
  if ! tee "${service_source}" >/dev/null <<EOF
[Unit]
Description=UConn Isomorphic Question LLM
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=5min
StartLimitBurst=3

[Service]
Type=simple
User=${APP_USER}
Group=${APP_USER}
WorkingDirectory=${CURRENT_RELEASE}/site
EnvironmentFile=-${CURRENT_RELEASE}/site/.env
EnvironmentFile=-${RUNTIME_CONFIG_FILE}
# Device secrets load last so they outrank both the release defaults and the
# persistent dashboard override file above.
EnvironmentFile=${RUNTIME_ENV_FILE}
Environment=RUNTIME_CONFIG_PATH=${RUNTIME_CONFIG_FILE}
# A tracked local-development default must never become an origin bypass. This
# follows every EnvironmentFile above so no file can re-enable the bypass.
Environment=ADMIN_LOCAL_DEV_MODE=false
Environment=HOST=127.0.0.1
Environment=PORT=${BACKEND_PORT}
Environment=ALLOWED_ORIGINS=${allowed_origins}
Environment=PYTHONDONTWRITEBYTECODE=1
ExecCondition=/bin/sh -c '/usr/bin/test ! -e ${ACTIVATION_STATE_FILE} && /usr/bin/test ! -L ${ACTIVATION_STATE_FILE} || /usr/bin/test -f ${ACTIVATION_RECOVERY_ALLOW_FILE}'
ExecCondition=/bin/sh -c '/usr/bin/test ! -e ${INTEGRITY_STATE_FILE} && /usr/bin/test ! -L ${INTEGRITY_STATE_FILE} || /usr/bin/test -f ${ACTIVATION_RECOVERY_ALLOW_FILE}'
ExecCondition=/bin/sh -c '/usr/bin/test ! -e ${RESTORE_STATE_FILE} && /usr/bin/test ! -L ${RESTORE_STATE_FILE} || { /usr/bin/test -f ${RESTORE_INSTALL_ALLOW_FILE} && /usr/bin/test ! -L ${RESTORE_INSTALL_ALLOW_FILE} && /usr/bin/test "\$(/usr/bin/stat -c "%%U:%%G:%%a" ${RESTORE_INSTALL_ALLOW_FILE} 2>/dev/null)" = "root:root:600"; }'
ExecCondition=/usr/bin/python3 -c "import os,sys; sys.exit(0 if os.getenv('OPENAI_API_KEY') and os.getenv('APP_ACCESS_TOKEN') else 1)"
ExecStart=${CURRENT_RELEASE}/.venv/bin/uvicorn backend.src.server:app --host 127.0.0.1 --port ${BACKEND_PORT}
Restart=on-failure
RestartSec=3
UMask=0027
SyslogIdentifier=${SERVICE_NAME}
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=${PERSIST_DIR}
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
EOF
  then
    rm -f "${service_source}"
    err "Could not stage the backend systemd unit."
    return 1
  fi

  if ! atomic_install_root_file "${service_source}" "${service_file}" 0644; then
    rm -f "${service_source}"
    err "Could not safely install the backend systemd unit."
    return 1
  fi
  rm -f "${service_source}"
  if ! sudo systemctl daemon-reload; then
    err "Could not reload systemd after installing the backend unit."
    return 1
  fi
  sudo systemctl enable "${SERVICE_NAME}"
  verify_systemd_unit_install "${SERVICE_NAME}.service" "${service_file}" "enabled" || return 1

  stop_existing_backend_service || return 1
  ensure_backend_port_available || return 1

  sudo systemctl reset-failed "${SERVICE_NAME}" 2>/dev/null || true
  sudo systemctl restart "${SERVICE_NAME}"
  if ! verify_systemd_unit_state "${SERVICE_NAME}.service" "loaded" "active" "enabled" \
    || ! wait_for_backend_health; then
    err "${SERVICE_NAME} did not remain in the exact enabled/active state."
    err "Check: sudo journalctl -u ${SERVICE_NAME} -n 50"
    return 1
  fi

  info "Backend service ${SERVICE_NAME} is installed."
}

restart_installed_backend_service() {
  local service_file="/etc/systemd/system/${SERVICE_NAME}.service"

  verify_systemd_unit_install \
    "${SERVICE_NAME}.service" "${service_file}" "enabled" || {
      err "Installed backend service definition is unsafe; run the interactive installer."
      return 1
    }
  sudo systemctl reset-failed "${SERVICE_NAME}" 2>/dev/null || true
  sudo systemctl restart "${SERVICE_NAME}"
  verify_systemd_unit_state "${SERVICE_NAME}.service" "loaded" "active" "enabled" \
    && wait_for_backend_health
}

activate_backend_service() {
  local automated="$1"
  if [[ "${automated}" == "true" ]]; then
    restart_installed_backend_service
  else
    install_backend_service
  fi
}

install_nginx_config() {
  banner "Nginx"

  local server_names="${LOCAL_DOMAIN} ${LOCAL_HOSTNAME} localhost 127.0.0.1"
  local listen_address="80"
  local had_conf=0
  local had_link=0
  local conf_backup=""
  local conf_source=""
  local link_target=""
  local resolved_target=""
  local default_link="${NGINX_ENABLED_DIR}/default"
  local default_conf="${NGINX_AVAILABLE_DIR}/default"
  local had_default_link=0
  local default_link_target=""
  local nginx_load_state nginx_active_state nginx_unit_file_state
  if [[ -n "${PUBLIC_HOSTNAME:-}" ]]; then
    server_names="${server_names} ${PUBLIC_HOSTNAME}"
    # Cloudflare Tunnel reaches this outbound-only loopback origin. Binding
    # publicly hosted mode to loopback prevents direct IP/Host access from
    # bypassing Cloudflare Access.
    listen_address="127.0.0.1:80"
  fi

  if [[ -L "${NGINX_CONF}" ]] \
    || { [[ -e "${NGINX_CONF}" ]] && [[ ! -f "${NGINX_CONF}" ]]; }; then
    err "Refusing unsafe nginx configuration path: ${NGINX_CONF}"
    return 1
  fi
  if [[ -e "${NGINX_LINK}" || -L "${NGINX_LINK}" ]] && [[ ! -L "${NGINX_LINK}" ]]; then
    err "Refusing non-symlinked nginx enabled path: ${NGINX_LINK}"
    return 1
  fi
  if [[ -e "${default_link}" || -L "${default_link}" ]] && [[ ! -L "${default_link}" ]]; then
    err "Refusing non-symlinked stock nginx enabled path: ${default_link}"
    return 1
  fi
  nginx_load_state="$(systemd_unit_property "nginx.service" "LoadState")" || return 1
  nginx_active_state="$(systemd_unit_property "nginx.service" "ActiveState")" || return 1
  nginx_unit_file_state="$(
    systemd_unit_property "nginx.service" "UnitFileState"
  )" || return 1
  if [[ "${nginx_load_state}" != "loaded" ]] \
    || [[ "${nginx_active_state}" != "active" && "${nginx_active_state}" != "inactive" ]] \
    || [[ "${nginx_unit_file_state}" != "enabled" \
      && "${nginx_unit_file_state}" != "disabled" ]]; then
    err "Nginx has unsafe pre-install state ${nginx_load_state}/${nginx_active_state}/${nginx_unit_file_state}."
    return 1
  fi

  audit_nginx_enabled_sites "${server_names}" || return 1

  if [[ -f "${NGINX_CONF}" ]]; then
    had_conf=1
    conf_backup="$(mktemp)"
    chmod 600 "${conf_backup}"
    if ! sudo cp --no-preserve=ownership,mode -- "${NGINX_CONF}" "${conf_backup}" \
      || ! chmod 600 "${conf_backup}" \
      || ! sudo cmp -s -- "${NGINX_CONF}" "${conf_backup}" \
      || ! python3 - "${conf_backup}" <<'PY'
import os
import sys

fd = os.open(sys.argv[1], os.O_RDWR)
try:
    os.fsync(fd)
finally:
    os.close(fd)
PY
    then
      err "Could not create and verify the nginx rollback copy."
      rm -f "${conf_backup}"
      return 1
    fi
  fi

  if [[ -L "${NGINX_LINK}" ]]; then
    if ! resolved_target="$(nginx_enabled_site_target "${NGINX_LINK}")" \
      || [[ "${resolved_target}" != "${NGINX_CONF}" ]]; then
      err "Managed nginx link does not resolve directly to ${NGINX_CONF}."
      [[ -z "${conf_backup}" ]] || rm -f "${conf_backup}"
      return 1
    fi
    had_link=1
    link_target="$(readlink "${NGINX_LINK}")"
  fi

  if [[ -L "${default_link}" ]]; then
    if ! resolved_target="$(nginx_enabled_site_target "${default_link}")" \
      || [[ "${resolved_target}" != "${default_conf}" ]]; then
      err "The conventional nginx default link points outside the stock site."
      [[ -z "${conf_backup}" ]] || rm -f "${conf_backup}"
      return 1
    fi
    had_default_link=1
    default_link_target="$(readlink "${default_link}")"
  fi

  conf_source="$(mktemp)"
  chmod 600 "${conf_source}" || {
    rm -f "${conf_backup}" "${conf_source}"
    return 1
  }
  if ! tee "${conf_source}" >/dev/null <<EOF
map \$uri \$uconn_expensive_client {
    default "";
    =/api/upload \$binary_remote_addr;
    ~^/api/download-zip/ \$binary_remote_addr;
    ~^/api/workflow/(start|rerun-from-agent|start-from-agent-input)\$ \$binary_remote_addr;
    ~^/api/(create-data|eval-batch)/start\$ \$binary_remote_addr;
    ~^/api/outputs/.+/compare-professor\$ \$binary_remote_addr;
}

map \$uri \$uconn_public_status_client {
    default "";
    =/api/openai/status \$binary_remote_addr;
}

limit_req_zone \$uconn_expensive_client zone=uconn_expensive:10m rate=6r/m;
limit_req_zone \$uconn_public_status_client zone=uconn_public_status:10m rate=30r/m;
limit_conn_zone \$binary_remote_addr zone=uconn_api_connections:10m;

server {
    listen ${listen_address} default_server;
    server_name _;
    return 444;
}

server {
    listen ${listen_address};
    server_name ${server_names};
    server_tokens off;

    # cloudflared is the only trusted proxy peer. Its client-IP header restores
    # per-client limits without allowing LAN clients to spoof arbitrary IPs.
    set_real_ip_from 127.0.0.1;
    set_real_ip_from ::1;
    real_ip_header CF-Connecting-IP;
    real_ip_recursive on;

    root ${CURRENT_RELEASE}/site/dist;
    index index.html;

    client_max_body_size 64m;
    client_body_timeout 60s;
    send_timeout 300s;
    limit_req_status 429;
    limit_conn_status 429;

    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "microphone=(), geolocation=()" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; connect-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; frame-src 'self' blob:; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'self'" always;

    # /run is cleared on reboot, but the fsynced activation transaction is
    # durable. Never serve a newly selected static tree while recovery is still
    # pending and the backend is deliberately blocked.
    if (-f ${ACTIVATION_STATE_FILE}) {
        return 503;
    }
    if (-e ${INTEGRITY_STATE_FILE}) {
        return 503;
    }
    set \$uconn_restore_block 0;
    if (-e ${RESTORE_STATE_FILE}) {
        set \$uconn_restore_block 1;
    }
    if (-f ${RESTORE_INSTALL_ALLOW_FILE}) {
        set \$uconn_restore_block 0;
    }
    if (\$uconn_restore_block = 1) {
        return 503;
    }

    location / {
        if (-f ${MAINTENANCE_FILE}) {
            return 503;
        }
        try_files \$uri \$uri/ /index.html;
    }

    location = /api {
        return 301 /api/;
    }

    location ~ ^/api/workflow/[^/]+/stream\$ {
        if (-f ${MAINTENANCE_FILE}) {
            return 503;
        }
        limit_conn uconn_api_connections 4;
        proxy_pass http://127.0.0.1:${BACKEND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-App-Access-Token \$http_x_app_access_token;
        proxy_read_timeout 3600s;
        proxy_send_timeout 300s;
        proxy_connect_timeout 5s;
        proxy_buffering off;
    }

    location /api/ {
        if (-f ${MAINTENANCE_FILE}) {
            return 503;
        }
        limit_req zone=uconn_expensive burst=2 nodelay;
        limit_req zone=uconn_public_status burst=5 nodelay;
        limit_conn uconn_api_connections 24;
        proxy_pass http://127.0.0.1:${BACKEND_PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header X-App-Access-Token \$http_x_app_access_token;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        proxy_connect_timeout 5s;
        proxy_buffering on;
        # Stream request bodies to the authenticated backend so rejected
        # uploads and multipart requests are not buffered to Pi storage first.
        proxy_request_buffering off;
    }
}
EOF
  then
    rm -f "${conf_backup}" "${conf_source}"
    err "Could not stage the generated nginx configuration."
    return 1
  fi

  if [[ "${had_default_link}" -eq 1 ]] \
    && ! atomic_remove_root_path "${default_link}" "${NGINX_ENABLED_DIR}" "symlink"; then
    err "Could not safely remove the stock nginx enabled-site link."
    if ! restore_nginx_install_state \
      "${had_conf}" "${conf_backup}" "${had_link}" "${link_target}" \
      "${had_default_link}" "${default_link_target}" "${default_link}"; then
      MAINTENANCE_RECOVERY_FAILED=1
      err "Nginx rollback failed; maintenance must remain enabled."
    fi
    rm -f "${conf_backup}" "${conf_source}"
    return 1
  fi
  if ! atomic_install_root_file "${conf_source}" "${NGINX_CONF}" 0644; then
    err "Could not safely install the generated nginx configuration."
    if ! restore_nginx_install_state \
      "${had_conf}" "${conf_backup}" "${had_link}" "${link_target}" \
      "${had_default_link}" "${default_link_target}" "${default_link}"; then
      MAINTENANCE_RECOVERY_FAILED=1
      err "Nginx rollback failed; maintenance must remain enabled."
    fi
    rm -f "${conf_backup}" "${conf_source}"
    return 1
  fi
  if ! atomic_install_nginx_symlink "${NGINX_CONF}" "${NGINX_LINK}"; then
    err "Could not safely install the managed nginx enabled-site link."
    if ! restore_nginx_install_state \
      "${had_conf}" "${conf_backup}" "${had_link}" "${link_target}" \
      "${had_default_link}" "${default_link_target}" "${default_link}"; then
      MAINTENANCE_RECOVERY_FAILED=1
      err "Nginx rollback failed; maintenance must remain enabled."
    fi
    rm -f "${conf_backup}" "${conf_source}"
    return 1
  fi
  rm -f "${conf_source}"

  local nginx_test_output=""
  if ! nginx_test_output="$(sudo nginx -t 2>&1)" \
    || grep -Eqi 'conflicting server name|duplicate listen|duplicate default server' \
      <<< "${nginx_test_output}"; then
    printf '%s\n' "${nginx_test_output}" >&2
    err "Generated nginx config failed validation; restoring the previous ${SERVICE_NAME} config."
    if ! restore_nginx_install_state \
      "${had_conf}" "${conf_backup}" "${had_link}" "${link_target}" \
      "${had_default_link}" "${default_link_target}" "${default_link}"; then
      MAINTENANCE_RECOVERY_FAILED=1
      err "Nginx rollback failed; maintenance must remain enabled."
    fi
    rm -f "${conf_backup}"
    return 1
  fi

  if ! sudo systemctl enable --now nginx \
    || ! sudo systemctl reload nginx \
    || ! verify_systemd_unit_state "nginx.service" "loaded" "active" "enabled"; then
    err "Nginx activation failed; restoring the prior enabled-site state."
    if ! rollback_nginx_activation \
      "${had_conf}" "${conf_backup}" "${had_link}" "${link_target}" \
      "${had_default_link}" "${default_link_target}" "${default_link}" \
      "${nginx_load_state}" "${nginx_active_state}" "${nginx_unit_file_state}"; then
      MAINTENANCE_RECOVERY_FAILED=1
      err "Nginx rollback failed; maintenance must remain enabled."
    fi
    rm -f "${conf_backup}"
    return 1
  fi

  if ! verify_nginx_app_hosts "${server_names}"; then
    err "Nginx did not route every exact app hostname; restoring the prior enabled-site state."
    if ! rollback_nginx_activation \
      "${had_conf}" "${conf_backup}" "${had_link}" "${link_target}" \
      "${had_default_link}" "${default_link_target}" "${default_link}" \
      "${nginx_load_state}" "${nginx_active_state}" "${nginx_unit_file_state}"; then
      MAINTENANCE_RECOVERY_FAILED=1
      err "Nginx rollback failed; maintenance must remain enabled."
    fi
    rm -f "${conf_backup}"
    return 1
  fi

  rm -f "${conf_backup}"
  info "Nginx is serving http://${LOCAL_DOMAIN}."
}

restore_nginx_install_state() {
  local had_conf="$1" conf_backup="$2" had_link="$3" link_target="$4"
  local had_default_link="$5" default_link_target="$6" default_link="$7"
  local failed=0

  if [[ "${had_conf}" -eq 1 ]]; then
    atomic_install_root_file "${conf_backup}" "${NGINX_CONF}" 0644 || failed=1
  else
    atomic_remove_root_path "${NGINX_CONF}" "${NGINX_AVAILABLE_DIR}" "regular" || failed=1
  fi
  if [[ "${had_link}" -eq 1 ]]; then
    atomic_install_nginx_symlink "${link_target}" "${NGINX_LINK}" || failed=1
  else
    atomic_remove_root_path "${NGINX_LINK}" "${NGINX_ENABLED_DIR}" "symlink" || failed=1
  fi
  if [[ "${had_default_link}" -eq 1 ]]; then
    atomic_install_nginx_symlink "${default_link_target}" "${default_link}" || failed=1
  else
    atomic_remove_root_path "${default_link}" "${NGINX_ENABLED_DIR}" "symlink" || failed=1
  fi
  [[ "${failed}" -eq 0 ]]
}

restore_nginx_systemd_state() {
  local expected_load="$1" expected_active="$2" expected_unit_file="$3"

  [[ "${expected_load}" == "loaded" ]] || return 1
  case "${expected_unit_file}" in
    enabled)
      sudo systemctl enable nginx || return 1
      ;;
    disabled)
      sudo systemctl disable nginx || return 1
      ;;
    *)
      return 1
      ;;
  esac
  case "${expected_active}" in
    active)
      sudo systemctl reset-failed nginx >/dev/null 2>&1 || true
      sudo systemctl restart nginx || return 1
      ;;
    inactive)
      sudo systemctl stop nginx || return 1
      ;;
    *)
      return 1
      ;;
  esac
  verify_systemd_unit_state \
    "nginx.service" "${expected_load}" "${expected_active}" "${expected_unit_file}"
}

rollback_nginx_activation() {
  local had_conf="$1" conf_backup="$2" had_link="$3" link_target="$4"
  local had_default_link="$5" default_link_target="$6" default_link="$7"
  local expected_load="$8" expected_active="$9" expected_unit_file="${10}"
  local failed=0

  restore_nginx_install_state \
    "${had_conf}" "${conf_backup}" "${had_link}" "${link_target}" \
    "${had_default_link}" "${default_link_target}" "${default_link}" || failed=1
  if [[ "${failed}" -eq 0 ]] && sudo nginx -t >/dev/null 2>&1; then
    if ! restore_nginx_systemd_state \
      "${expected_load}" "${expected_active}" "${expected_unit_file}"; then
      failed=1
    fi
  else
    failed=1
  fi
  if [[ "${failed}" -ne 0 ]]; then
    sudo systemctl disable --now nginx >/dev/null 2>&1 || true
    verify_systemd_unit_state \
      "nginx.service" "loaded" "inactive" "disabled" >/dev/null 2>&1 || true
    return 1
  fi
}

audit_nginx_enabled_sites() {
  local server_names="$1" site relation
  local default_link="${NGINX_ENABLED_DIR}/default"
  local -a expected_names=()

  read -r -a expected_names <<< "${server_names}"
  for site in "${NGINX_ENABLED_DIR}"/*; do
    if [[ ! -e "${site}" && ! -L "${site}" ]]; then
      continue
    fi
    if [[ ! -L "${site}" ]] || ! nginx_enabled_site_target_is_safe "${site}"; then
      err "Enabled nginx site is not a contained regular-file symlink: ${site}"
      return 1
    fi
    if [[ "${site}" == "${NGINX_LINK}" ]] || [[ "${site}" == "${default_link}" ]]; then
      continue
    fi

    if ! relation="$(
      python3 - "${site}" "${expected_names[@]}" <<'PY'
import fnmatch
import re
import shlex
import sys
from pathlib import Path

path = Path(sys.argv[1])
expected = sys.argv[2:]
try:
    text = path.read_text(encoding="utf-8")
except (OSError, UnicodeError) as exc:
    raise SystemExit(f"could not inspect {path}: {exc}")

text = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
relations = set()
for match in re.finditer(r"\bserver_name\s+([^;]+);", text):
    try:
        tokens = shlex.split(match.group(1), comments=False, posix=True)
    except ValueError:
        relations.add("ambiguous")
        continue
    for token in tokens:
        if token.startswith("~"):
            relations.add("ambiguous")
        elif token.startswith(".") and any(
            name == token[1:] or name.endswith(token) for name in expected
        ):
            relations.add("host")
        elif any(token == name or fnmatch.fnmatchcase(name, token) for name in expected):
            relations.add("host")
for match in re.finditer(r"\blisten\s+([^;]+);", text):
    if "default_server" in match.group(1).split():
        relations.add("default")
print(",".join(sorted(relations)))
PY
    )"; then
      err "Could not safely inspect enabled nginx site ${site}."
      return 1
    fi
    if [[ "${relation}" == *"host"* ]]; then
      err "Enabled nginx site ${site} already claims an exact app hostname; disable it explicitly and rerun."
      return 1
    fi
    if [[ "${relation}" == *"default"* || "${relation}" == *"ambiguous"* ]]; then
      err "Enabled nginx site ${site} has a default or regex server that conflicts with the rejecting catch-all; review it explicitly."
      return 1
    fi
  done
}

verify_nginx_app_hosts() {
  local server_names="$1" hostname status expected_status="200"
  local -a expected_names=()

  if sudo test -e "${MAINTENANCE_FILE}" || sudo test -L "${MAINTENANCE_FILE}" \
    || sudo test -e "${ACTIVATION_STATE_FILE}" || sudo test -L "${ACTIVATION_STATE_FILE}" \
    || sudo test -e "${RESTORE_STATE_FILE}" || sudo test -L "${RESTORE_STATE_FILE}"; then
    expected_status="503"
  fi
  read -r -a expected_names <<< "${server_names}"
  for hostname in "${expected_names[@]}"; do
    if ! status="$(
      "${CURL_BIN}" -q --noproxy '*' --proto '=http' \
        --connect-timeout 2 --max-time 10 -sS -o /dev/null -w '%{http_code}' \
        -H "Host: ${hostname}" "http://127.0.0.1/api/health"
    )" || [[ "${status}" != "${expected_status}" ]]; then
      err "Nginx exact-host check for ${hostname} returned ${status:-unreachable}, expected ${expected_status}."
      return 1
    fi
  done
}

verify_local_site() {
  banner "Local Verification"

  sleep 2

  if ! systemctl is-active --quiet "${SERVICE_NAME}"; then
    err "${SERVICE_NAME} is not running. Check: sudo journalctl -u ${SERVICE_NAME} -n 50"
    exit 1
  fi

  if ! systemctl is-active --quiet nginx; then
    err "nginx is not running. Check: sudo journalctl -u nginx -n 50"
    exit 1
  fi

  if ! "${CURL_BIN}" -q --noproxy '*' --proto '=http' -fsS \
    "http://127.0.0.1:${BACKEND_PORT}/api/health" >/dev/null; then
    err "Backend health check failed at http://127.0.0.1:${BACKEND_PORT}/api/health."
    err "Check: sudo journalctl -u ${SERVICE_NAME} -n 50"
    exit 1
  fi

  if ! "${CURL_BIN}" -q --noproxy '*' --proto '=http' -fsS \
    --resolve "${LOCAL_DOMAIN}:80:127.0.0.1" \
    "http://${LOCAL_DOMAIN}/api/health" >/dev/null; then
    err "Local nginx health check failed for http://${LOCAL_DOMAIN}/api/health."
    err "Check: sudo nginx -T | grep -A20 'server_name ${LOCAL_DOMAIN}'"
    exit 1
  fi

  if ! "${CURL_BIN}" -q --noproxy '*' --proto '=http' -fsS \
      -H "Host: 127.0.0.1" "http://127.0.0.1/api/health" >/dev/null \
    || ! "${CURL_BIN}" -q --noproxy '*' --proto '=http' -fsS \
      -H "Host: localhost" "http://127.0.0.1/api/health" >/dev/null; then
    err "Nginx did not route the documented localhost/SSH-tunnel hostnames to this app."
    return 1
  fi
  info "Site is confirmed through localhost and http://${LOCAL_DOMAIN}"
}

save_deploy_config() {
  local temporary
  temporary="$(mktemp)"
  chmod 600 "${temporary}"
  save_config_var "${temporary}" "SOURCE_REPO_DIR" "${REPO_DIR}"
  save_config_var "${temporary}" "LOCAL_HOSTNAME" "${LOCAL_HOSTNAME}"
  save_config_var "${temporary}" "LOCAL_DOMAIN" "${LOCAL_DOMAIN}"
  save_config_var "${temporary}" "DEPLOY_DIR" "${DEPLOY_DIR}"
  save_config_var "${temporary}" "SERVICE_NAME" "${SERVICE_NAME}"
  save_config_var "${temporary}" "BACKEND_PORT" "${BACKEND_PORT}"
  save_config_var "${temporary}" "PERSIST_DIR" "${PERSIST_DIR}"
  save_config_var "${temporary}" "RELEASE_RETENTION" "${RELEASE_RETENTION}"
  save_config_var "${temporary}" "CLOUDFLARED_SERVICE_NAME" "${CLOUDFLARED_SERVICE_NAME}"
  save_config_var "${temporary}" "CLOUDFLARED_CONFIG_FILE" "${CLOUDFLARED_CONFIG_FILE}"
  save_config_var "${temporary}" "AUTO_UPDATE" "${AUTO_UPDATE:-0}"
  if [[ -n "${UPDATE_REMOTE_URL:-}" ]]; then
    save_config_var "${temporary}" "UPDATE_REMOTE_URL" "${UPDATE_REMOTE_URL}"
  fi
  if [[ -n "${TUNNEL_NAME:-}" ]]; then
    save_config_var "${temporary}" "TUNNEL_NAME" "${TUNNEL_NAME}"
  fi
  if [[ -n "${PUBLIC_HOSTNAME:-}" ]]; then
    save_config_var "${temporary}" "PUBLIC_HOSTNAME" "${PUBLIC_HOSTNAME}"
  fi
  if [[ -n "${TUNNEL_UUID:-}" ]]; then
    save_config_var "${temporary}" "TUNNEL_UUID" "${TUNNEL_UUID}"
  fi
  sudo install -d -o root -g root -m 755 "${RUNTIME_CONFIG_DIR}"
  sudo python3 - "${temporary}" "${DEPLOY_CONFIG_FILE}" <<'PY'
import os
import sys
import tempfile
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
if target.is_symlink() or (target.exists() and not target.is_file()):
    raise SystemExit("unsafe deployment config target")
payload = source.read_bytes()
fd, temporary_name = tempfile.mkstemp(prefix=".deploy.conf.", dir=target.parent)
staged = Path(temporary_name)
try:
    os.fchmod(fd, 0o644)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chown(staged, 0, 0)
    os.replace(staged, target)
    directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    try:
        staged.unlink()
    except FileNotFoundError:
        pass
PY
  rm -f "${temporary}"
}

save_config_var() {
  local target="$1"
  local name="$2"
  local value="$3"

  if [[ "${value}" == *$'\n'* || "${value}" == *$'\r'* ]]; then
    err "Refusing multiline deployment setting ${name}."
    return 1
  fi
  printf '%s=%s\n' "${name}" "${value}" >> "${target}"
}

install_cloudflared() {
  if [[ -f "${CLOUDFLARED_BIN}" ]] && [[ ! -L "${CLOUDFLARED_BIN}" ]] \
    && [[ -x "${CLOUDFLARED_BIN}" ]] \
    && [[ "$(stat -c '%U:%G' "${CLOUDFLARED_BIN}" 2>/dev/null || true)" == "root:root" ]] \
    && [[ -z "$(find "${CLOUDFLARED_BIN}" -maxdepth 0 -perm /022 -print -quit 2>/dev/null)" ]]; then
    info "cloudflared already installed: $("${CLOUDFLARED_BIN}" --version 2>&1 | head -1)"
    return
  fi
  if command -v cloudflared >/dev/null 2>&1; then
    warn "Ignoring untrusted cloudflared executable outside ${CLOUDFLARED_BIN}."
  fi

  apt_install_if_missing curl
  apt_install_if_missing ca-certificates update-ca-certificates

  local key_file
  key_file="$(mktemp)"
  info "Installing cloudflared from Cloudflare's signed apt repository."
  curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg -o "${key_file}"
  verify_gpg_primary_fingerprint \
    "${key_file}" "${CLOUDFLARE_KEY_FINGERPRINT}" "Cloudflare"
  sudo install -o root -g root -m 644 "${key_file}" /usr/share/keyrings/cloudflare-main.gpg
  rm -f "${key_file}"
  echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
    | sudo tee /etc/apt/sources.list.d/cloudflared.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y cloudflared
  if [[ ! -f "${CLOUDFLARED_BIN}" ]] || [[ -L "${CLOUDFLARED_BIN}" ]] \
    || [[ ! -x "${CLOUDFLARED_BIN}" ]] \
    || [[ "$(stat -c '%U:%G' "${CLOUDFLARED_BIN}" 2>/dev/null || true)" != "root:root" ]] \
    || [[ -n "$(find "${CLOUDFLARED_BIN}" -maxdepth 0 -perm /022 -print -quit 2>/dev/null)" ]]; then
    err "The signed apt package did not install a trusted ${CLOUDFLARED_BIN}."
    return 1
  fi
}

ensure_cloudflare_authenticated() {
  if [[ -r "${CLOUDFLARED_USER_DIR}/cert.pem" ]] || [[ -r "${CLOUDFLARED_SYSTEM_DIR}/cert.pem" ]]; then
    info "Cloudflare credentials already exist."
    return
  fi

  if [[ -f "${CLOUDFLARED_SYSTEM_DIR}/cert.pem" ]]; then
    warn "${CLOUDFLARED_SYSTEM_DIR}/cert.pem exists but is not readable by this user."
    warn "Logging in again will create ${CLOUDFLARED_USER_DIR}/cert.pem for tunnel management."
  fi

  warn "Cloudflare login is required. A browser URL will be shown."
  "${CLOUDFLARED_BIN}" tunnel login

  if [[ ! -r "${CLOUDFLARED_USER_DIR}/cert.pem" ]]; then
    err "Cloudflare login did not create ${CLOUDFLARED_USER_DIR}/cert.pem."
    exit 1
  fi
}

cloudflared_origin_cert_file() {
  if [[ -r "${CLOUDFLARED_USER_DIR}/cert.pem" ]]; then
    printf '%s\n' "${CLOUDFLARED_USER_DIR}/cert.pem"
  elif [[ -r "${CLOUDFLARED_SYSTEM_DIR}/cert.pem" ]]; then
    printf '%s\n' "${CLOUDFLARED_SYSTEM_DIR}/cert.pem"
  fi
}

cloudflared_with_cert() {
  local cert_file
  cert_file="$(cloudflared_origin_cert_file)"

  if [[ -n "${cert_file}" ]]; then
    TUNNEL_ORIGIN_CERT="${cert_file}" "${CLOUDFLARED_BIN}" "$@"
  else
    "${CLOUDFLARED_BIN}" "$@"
  fi
}

list_active_tunnels() {
  if cloudflared_with_cert tunnel list -o json 2>/dev/null | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
if isinstance(data, dict):
    tunnels = data.get("result") or data.get("tunnels") or []
else:
    tunnels = data
if not isinstance(tunnels, list):
    raise SystemExit(1)
for tunnel in tunnels:
    if not isinstance(tunnel, dict):
        continue
    if tunnel.get("deleted_at"):
        continue
    tunnel_id = tunnel.get("id") or tunnel.get("uuid") or ""
    tunnel_name = tunnel.get("name") or ""
    if tunnel_id and tunnel_name:
        print(f"{tunnel_id}\t{tunnel_name}")
' 2>/dev/null; then
    return
  fi

  cloudflared_with_cert tunnel list 2>/dev/null | awk 'NR > 1 && $1 ~ /^[[:xdigit:]-]+$/ && $2 != "" { print $1 "\t" $2 }' || true
}

extract_tunnel_uuid() {
  sed -nE \
    -e 's/.*Created tunnel .* with id ([[:xdigit:]-]{36}).*/\1/p' \
    -e 's|.*Tunnel credentials written to .*/([[:xdigit:]-]{36})\.json.*|\1|p' \
    -e 's/^[[:space:]]*(ID|id|Tunnel ID|TunnelID)[^[:xdigit:]]*([[:xdigit:]-]{36}).*/\2/p' \
    -e 's/.*"([[:xdigit:]-]{36})".*/\1/p' \
    | tail -n 1
}

is_tunnel_uuid() {
  [[ "$1" =~ ^[[:xdigit:]]{8}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{4}-[[:xdigit:]]{12}$ ]]
}

tunnel_credential_exists() {
  local uuid="$1"

  [[ -f "${CLOUDFLARED_USER_DIR}/${uuid}.json" ]] || [[ -f "${CLOUDFLARED_SYSTEM_DIR}/${uuid}.json" ]]
}

find_tunnel_uuid_from_info() {
  local tunnel_name="$1"
  local info_output uuid

  if info_output="$(cloudflared_with_cert tunnel info "${tunnel_name}" -o json 2>/dev/null)"; then
    uuid="$(printf '%s\n' "${info_output}" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    raise SystemExit(1)
for key in ("id", "uuid", "ID", "TunnelID"):
    value = data.get(key)
    if value:
        print(value)
        break
' 2>/dev/null || true)"
    if is_tunnel_uuid "${uuid}"; then
      printf '%s\n' "${uuid}"
      return
    fi
  fi

  info_output="$(cloudflared_with_cert tunnel info "${tunnel_name}" 2>/dev/null || true)"
  printf '%s\n' "${info_output}" | extract_tunnel_uuid
}

cloudflared_service_units() {
  local unit_files loaded_units all_units unit exec_start

  if ! unit_files="$(
    systemctl list-unit-files --type=service --no-legend 2>/dev/null
  )"; then
    err "Could not enumerate installed services while checking Cloudflare connectors."
    return 1
  fi
  if ! loaded_units="$(
    systemctl list-units --type=service --all --no-legend 2>/dev/null
  )"; then
    err "Could not enumerate loaded services while checking Cloudflare connectors."
    return 1
  fi

  all_units="$(
    {
      printf '%s\n' "${unit_files}" \
        | awk 'NF && $1 !~ /@\.service$/ && ($1 ~ /cloudflared/ || $2 ~ /^(enabled|enabled-runtime|linked|linked-runtime|alias)$/) { print $1 }'
      printf '%s\n' "${loaded_units}" | awk 'NF { print $1 }'
    } | sort -u
  )"
  while IFS= read -r unit; do
    [[ -n "${unit}" ]] || continue
    if [[ "${unit}" == *cloudflared* ]]; then
      printf '%s\n' "${unit}"
      continue
    fi
    if ! exec_start="$(
      systemctl show "${unit}" --property=ExecStart --value 2>/dev/null
    )"; then
      err "Could not inspect ExecStart for systemd unit ${unit}."
      return 1
    fi
    if [[ "${exec_start}" =~ (^|[/[:space:]=;\{])cloudflared([[:space:];\}]|$) ]]; then
      printf '%s\n' "${unit}"
    fi
  done <<< "${all_units}"
}

cloudflared_service_config_paths() {
  local unit="$1" unit_text

  if ! unit_text="$(systemctl cat "${unit}" 2>/dev/null)"; then
    err "Could not inspect Cloudflare service unit ${unit}."
    return 1
  fi

  if [[ "${unit}" == "cloudflared.service" ]]; then
    printf '%s\n' \
      "${CLOUDFLARED_SYSTEM_DIR}/config.yml" \
      "${CLOUDFLARED_SYSTEM_DIR}/config.yaml"
  fi
  UNIT_TEXT="${unit_text}" python3 - <<'PY'
import os
import shlex

text = os.environ["UNIT_TEXT"].replace("\\\n", " ")
paths = set()
for raw_line in text.splitlines():
    line = raw_line.strip()
    if not line.startswith("ExecStart="):
        continue
    try:
        tokens = shlex.split(line.partition("=")[2], comments=True, posix=True)
    except ValueError as exc:
        raise SystemExit(f"could not parse cloudflared ExecStart: {exc}")
    for index, token in enumerate(tokens):
        if token == "--config" and index + 1 < len(tokens):
            paths.add(tokens[index + 1])
        elif token.startswith("--config="):
            paths.add(token.partition("=")[2])
for path in sorted(paths):
    print(path)
PY
}

cloudflared_config_relation() {
  local config_file="$1"

  sudo env \
    EXPECTED_HOSTNAME="${PUBLIC_HOSTNAME:-}" \
    EXPECTED_TUNNEL_UUID="${TUNNEL_UUID:-}" \
    python3 - "${config_file}" <<'PY'
import os
import re
import shlex
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

path = Path(sys.argv[1])
try:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
except OSError as exc:
    raise SystemExit(f"could not open cloudflared config {path}: {exc}")
try:
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1024 * 1024:
        raise SystemExit(f"unsafe cloudflared config {path}")
    payload = os.read(fd, metadata.st_size + 1).decode("utf-8")
finally:
    os.close(fd)

hostname = os.environ["EXPECTED_HOSTNAME"].lower().rstrip(".")
tunnel_uuid = os.environ["EXPECTED_TUNNEL_UUID"].lower()
hostnames = set()
tunnel_ids = set()
services = set()
recognized = False

def scalar(raw: str) -> str:
    try:
        values = shlex.split(raw, comments=True, posix=True)
    except ValueError as exc:
        raise SystemExit(f"ambiguous cloudflared YAML scalar in {path}: {exc}")
    return values[0] if len(values) == 1 else ""

for raw_line in payload.splitlines():
    line = raw_line.strip()
    if not line or line.startswith("#") or ":" not in line:
        continue
    key, value = line.split(":", 1)
    key = key.lstrip("- ").strip()
    value = scalar(value.strip())
    if key == "hostname":
        recognized = True
        if value:
            hostnames.add(value.lower().rstrip("."))
    elif key == "tunnel":
        recognized = True
        if value:
            tunnel_ids.update(match.lower() for match in re.findall(
                r"(?<![0-9a-f-])[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}(?![0-9a-f-])",
                value,
                flags=re.IGNORECASE,
            ))
    elif key == "credentials-file":
        recognized = True
        if value:
            match = re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                Path(value).stem,
                flags=re.IGNORECASE,
            )
            if match:
                tunnel_ids.add(match.group(0).lower())
    elif key == "service":
        recognized = True
        if value:
            services.add(value)

def is_app_origin(raw: str) -> bool:
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
        and port in (None, 80)
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
    )

if (hostname and hostname in hostnames) or (tunnel_uuid and tunnel_uuid in tunnel_ids):
    print("target")
elif any(is_app_origin(service) for service in services):
    print("origin")
elif recognized:
    print("unrelated")
else:
    print("opaque")
PY
}

cloudflared_service_relation() {
  local unit="$1" unit_text unit_relation config_paths config_file relation
  local saw_config=0 saw_unrelated=0 saw_opaque=0

  if ! unit_text="$(systemctl cat "${unit}" 2>/dev/null)"; then
    err "Could not inspect Cloudflare service unit ${unit}."
    return 1
  fi
  if ! unit_relation="$(
    UNIT_TEXT="${unit_text}" \
      EXPECTED_HOSTNAME="${PUBLIC_HOSTNAME:-}" \
      EXPECTED_TUNNEL_UUID="${TUNNEL_UUID:-}" \
      python3 - <<'PY'
import os
import re
import shlex
from urllib.parse import urlsplit

text = os.environ["UNIT_TEXT"].replace("\\\n", " ")
hostname = os.environ["EXPECTED_HOSTNAME"].lower().rstrip(".")
tunnel_uuid = os.environ["EXPECTED_TUNNEL_UUID"].lower()
target = False
origin = False
opaque = False

def is_app_origin(raw: str) -> bool:
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and (parsed.hostname or "").lower() in {"127.0.0.1", "localhost", "::1"}
        and port in (None, 80)
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment
    )

for raw_line in text.splitlines():
    line = raw_line.strip()
    if not line.startswith("ExecStart="):
        continue
    try:
        tokens = shlex.split(line.partition("=")[2], comments=True, posix=True)
    except ValueError as exc:
        raise SystemExit(f"could not parse cloudflared ExecStart: {exc}")
    for index, token in enumerate(tokens):
        next_token = tokens[index + 1] if index + 1 < len(tokens) else ""
        if token == "--token" or token.startswith("--token="):
            opaque = True
        if token == "--hostname":
            value = next_token
        elif token.startswith("--hostname="):
            value = token.partition("=")[2]
        else:
            value = ""
        if value and hostname and value.lower().rstrip(".") == hostname:
            target = True
        if token in {"--url", "--origin"}:
            value = next_token
        elif token.startswith("--url=") or token.startswith("--origin="):
            value = token.partition("=")[2]
        else:
            value = ""
        if value and is_app_origin(value):
            origin = True
        if tunnel_uuid and (
            token.lower() == tunnel_uuid
            or re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                token,
                flags=re.IGNORECASE,
            )
            and token.lower() == tunnel_uuid
        ):
            target = True
if target:
    print("target")
elif origin:
    print("origin")
elif opaque:
    print("opaque")
else:
    print("unrelated")
PY
  )"; then
    err "Could not parse Cloudflare service unit ${unit}."
    return 1
  fi
  if [[ "${unit_relation}" == "target" || "${unit_relation}" == "origin" ]]; then
    printf '%s\n' "${unit_relation}"
    return 0
  fi
  [[ "${unit_relation}" == "opaque" ]] && saw_opaque=1

  if ! config_paths="$(cloudflared_service_config_paths "${unit}")"; then
    return 1
  fi
  while IFS= read -r config_file; do
    [[ -n "${config_file}" ]] || continue
    if [[ ! -f "${config_file}" ]] || [[ -L "${config_file}" ]]; then
      saw_opaque=1
      continue
    fi
    saw_config=1
    relation="$(cloudflared_config_relation "${config_file}")" || return 1
    case "${relation}" in
      target|origin)
        printf '%s\n' "${relation}"
        return 0
        ;;
      opaque)
        saw_opaque=1
        ;;
      unrelated)
        saw_unrelated=1
        ;;
      *)
        err "Unexpected Cloudflare relation ${relation} for ${config_file}."
        return 1
        ;;
    esac
  done <<< "${config_paths}"

  if [[ "${saw_opaque}" -eq 1 || "${saw_config}" -eq 0 ]]; then
    printf '%s\n' "opaque"
  elif [[ "${saw_unrelated}" -eq 1 ]]; then
    printf '%s\n' "unrelated"
  else
    printf '%s\n' "opaque"
  fi
}

stop_conflicting_cloudflared_services() {
  local unit units load_state active_state unit_file_state relation process_count

  if ! units="$(cloudflared_service_units)"; then
    return 1
  fi

  while IFS= read -r unit; do
    if [[ -z "${unit}" ]] || [[ "${unit}" == "${CLOUDFLARED_SERVICE_NAME}.service" ]]; then
      continue
    fi

    load_state="$(systemd_unit_property "${unit}" "LoadState")" || return 1
    [[ "${load_state}" == "not-found" ]] && continue
    active_state="$(systemd_unit_property "${unit}" "ActiveState")" || return 1
    unit_file_state="$(systemd_unit_property "${unit}" "UnitFileState")" || return 1
    if [[ "${active_state}" == "inactive" ]] \
      && [[ "${unit_file_state}" =~ ^(disabled|masked)$ ]]; then
      continue
    fi
    relation="$(cloudflared_service_relation "${unit}")" || return 1
    if [[ "${relation}" == "target" ]]; then
      warn "Stopping ${unit}; it exactly matches the selected hostname or tunnel UUID."
      disable_systemd_unit_if_present "${unit}" || return 1
      continue
    fi
    err "Cloudflare service ${unit} is ${active_state}/${unit_file_state} and classified ${relation}."
    err "Disable and review this connector explicitly; the installer will not modify an ambiguous or unrelated service."
    return 1
  done <<< "${units}"

  if ! process_count="$(
    sudo python3 - <<'PY'
import os
from pathlib import Path

count = 0
for comm_path in Path("/proc").glob("[0-9]*/comm"):
    try:
        comm = comm_path.read_text(encoding="utf-8").strip()
        executable = os.path.basename(os.readlink(comm_path.parent / "exe"))
    except (FileNotFoundError, PermissionError, ProcessLookupError, OSError):
        continue
    if comm == "cloudflared" or executable == "cloudflared":
        count += 1
print(count)
PY
  )"; then
    err "Could not inspect running processes for unmanaged Cloudflare connectors."
    return 1
  fi
  if [[ ! "${process_count}" =~ ^[0-9]+$ ]] || (( process_count != 0 )); then
    err "An unmanaged cloudflared process is still running; public/local-only transition refused."
    return 1
  fi
}

print_existing_tunnels() {
  local tunnels="$1"
  local tunnel_uuid tunnel_name

  info "Existing Cloudflare tunnels found:"
  while IFS=$'\t' read -r tunnel_uuid tunnel_name; do
    if [[ -n "${tunnel_uuid}" ]] && [[ -n "${tunnel_name}" ]]; then
      printf '  - %s (%s)\n' "${tunnel_name}" "${tunnel_uuid}"
    fi
  done <<< "${tunnels}"
}

find_tunnel_uuid() {
  local tunnel_name="$1"
  local uuid=""

  uuid="$(list_active_tunnels | awk -F '\t' -v name="${tunnel_name}" '$2 == name { print $1; exit }')"
  if [[ -z "${uuid}" ]]; then
    uuid="$(cloudflared_with_cert tunnel list --name "${tunnel_name}" 2>/dev/null | awk -v name="${tunnel_name}" '$1 ~ /^[[:xdigit:]-]{36}$/ && $0 ~ name { print $1; exit }' || true)"
  fi
  if [[ -z "${uuid}" ]]; then
    uuid="$(find_tunnel_uuid_from_info "${tunnel_name}")"
  fi

  printf '%s\n' "${uuid}"
}

create_cloudflare_tunnel_named() {
  local tunnel_name="$1"
  local create_output uuid

  info "Creating Cloudflare tunnel ${tunnel_name}."
  if ! create_output="$(cloudflared_with_cert tunnel create "${tunnel_name}" 2>&1)"; then
    printf '%s\n' "${create_output}"
    return 1
  fi

  printf '%s\n' "${create_output}"
  uuid="$(printf '%s\n' "${create_output}" | extract_tunnel_uuid)"

  if [[ -z "${uuid}" ]]; then
    uuid="$(find_tunnel_uuid "${tunnel_name}")"
  fi

  if [[ -z "${uuid}" ]] || ! is_tunnel_uuid "${uuid}"; then
    err "Could not determine the Cloudflare tunnel UUID for ${tunnel_name}."
    return 1
  fi

  TUNNEL_NAME="${tunnel_name}"
  TUNNEL_UUID="${uuid}"
  return 0
}

select_cloudflare_tunnel_name() {
  local configured_name="${TUNNEL_NAME:-}"
  local tunnels matching_uuid selected_name default_name

  tunnels="$(list_active_tunnels || true)"

  if [[ -n "${configured_name}" ]]; then
    if [[ -n "${tunnels}" ]]; then
      matching_uuid="$(awk -F '\t' -v name="${configured_name}" '$2 == name { print $1; exit }' <<< "${tunnels}")"
      if [[ -n "${matching_uuid}" ]]; then
        info "Using configured existing Cloudflare tunnel ${configured_name} (${matching_uuid})."
        ALLOW_EXISTING_TUNNEL=1
        TUNNEL_NAME="${configured_name}"
        return
      fi

      warn "Configured Cloudflare tunnel ${configured_name} was not found in this account."
    else
      info "Using configured Cloudflare tunnel name ${configured_name}; it will be created if needed."
      TUNNEL_NAME="${configured_name}"
      return
    fi
  fi

  if [[ -z "${tunnels}" ]]; then
    TUNNEL_NAME="$(prompt_with_default "Cloudflare tunnel name" "${configured_name:-uconn-isomorphic-${LOCAL_HOSTNAME}}")"
    return
  fi

  print_existing_tunnels "${tunnels}"
  default_name="uconn-isomorphic-${LOCAL_HOSTNAME}"
  if awk -F '\t' -v name="${default_name}" '$2 == name { found=1 } END { exit !found }' <<< "${tunnels}"; then
    default_name="${default_name}-$(date -u +%Y%m%d%H%M%S)"
  fi
  selected_name="$(prompt_with_default "New dedicated Cloudflare tunnel name" "${default_name}")"
  matching_uuid="$(awk -F '\t' -v name="${selected_name}" '$2 == name { print $1; exit }' <<< "${tunnels}")"
  if [[ -n "${matching_uuid}" ]]; then
    warn "Tunnel ${selected_name} already exists and may serve another connector or application."
    if ! confirm_default_no "Explicitly reuse existing tunnel ${selected_name} only if it is dedicated to this app?"; then
      err "Existing tunnel was not reused. Rerun and choose a new dedicated name."
      return 1
    fi
    ALLOW_EXISTING_TUNNEL=1
  fi
  TUNNEL_NAME="${selected_name}"
}

ensure_cloudflare_tunnel() {
  local tunnel_name="$1"
  local replacement_name uuid

  if [[ -n "${TUNNEL_UUID:-}" ]] && is_tunnel_uuid "${TUNNEL_UUID}"; then
    if tunnel_credential_exists "${TUNNEL_UUID}"; then
      info "Using configured Cloudflare tunnel ${tunnel_name} (${TUNNEL_UUID})."
      return
    fi

    warn "Configured tunnel ${TUNNEL_UUID} has no local credential JSON; creating a replacement tunnel."
    unset TUNNEL_UUID
  fi

  uuid="$(find_tunnel_uuid "${tunnel_name}")"

  if [[ -n "${uuid}" ]]; then
    if [[ "${ALLOW_EXISTING_TUNNEL}" -ne 1 ]]; then
      err "Refusing to reuse existing tunnel ${tunnel_name} without explicit confirmation."
      return 1
    fi
    if tunnel_credential_exists "${uuid}"; then
      info "Using existing Cloudflare tunnel ${tunnel_name} (${uuid})."
      TUNNEL_UUID="${uuid}"
      return
    fi

    warn "Existing Cloudflare tunnel ${tunnel_name} (${uuid}) has no local credential JSON; creating a replacement tunnel."
  fi

  if create_cloudflare_tunnel_named "${tunnel_name}"; then
    return
  fi

  replacement_name="${tunnel_name}-${LOCAL_HOSTNAME}"
  warn "Tunnel name ${tunnel_name} is unavailable or unusable locally. Trying ${replacement_name}."
  if create_cloudflare_tunnel_named "${replacement_name}"; then
    return
  fi

  replacement_name="${tunnel_name}-${LOCAL_HOSTNAME}-$(date -u +%Y%m%d%H%M%S)"
  warn "Tunnel name ${replacement_name%-*} is unavailable. Trying ${replacement_name}."
  if create_cloudflare_tunnel_named "${replacement_name}"; then
    return
  fi

  err "Could not create a usable Cloudflare tunnel."
  exit 1
}

copy_cloudflare_credentials() {
  local user_credential="${CLOUDFLARED_USER_DIR}/${TUNNEL_UUID}.json"
  local system_credential="${CLOUDFLARED_SYSTEM_DIR}/${TUNNEL_UUID}.json"
  local invoking_uid cloud_uid cloud_gid
  invoking_uid="$(id -u)"
  cloud_uid="$(id -u "${CLOUDFLARED_USER}")"
  cloud_gid="$(id -g "${CLOUDFLARED_USER}")"
  sudo install -d -o root -g root -m 755 "${CLOUDFLARED_SYSTEM_DIR}"

  if [[ -e "${user_credential}" || -L "${user_credential}" ]]; then
    sudo env \
      SOURCE_FILE="${user_credential}" \
      DEST_FILE="${system_credential}" \
      EXPECTED_UID="${invoking_uid}" \
      DEST_UID="${cloud_uid}" \
      DEST_GID="${cloud_gid}" \
      EXPECTED_TUNNEL_UUID="${TUNNEL_UUID}" \
      python3 - <<'PY'
import json
import os
import stat
import tempfile
from pathlib import Path

source = Path(os.environ["SOURCE_FILE"])
destination = Path(os.environ["DEST_FILE"])
source_fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
try:
    metadata = os.fstat(source_fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != int(os.environ["EXPECTED_UID"])
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise OSError("unsafe Cloudflare credential source")
    data = bytearray()
    while chunk := os.read(source_fd, 1024 * 1024):
        data.extend(chunk)
        if len(data) > 1024 * 1024:
            raise OSError("Cloudflare credential is too large")
finally:
    os.close(source_fd)
payload = json.loads(data.decode("utf-8"))
if not isinstance(payload, dict) or not all(
    isinstance(payload.get(key), str) and payload[key]
    for key in ("AccountTag", "TunnelID", "TunnelSecret")
):
    raise OSError("invalid Cloudflare credential JSON")
if payload["TunnelID"].lower() != os.environ["EXPECTED_TUNNEL_UUID"].lower():
    raise OSError("Cloudflare credential UUID does not match selected tunnel")
try:
    destination_metadata = destination.lstat()
except FileNotFoundError:
    pass
else:
    if not stat.S_ISREG(destination_metadata.st_mode):
        raise OSError("unsafe Cloudflare credential destination")
fd, temporary_name = tempfile.mkstemp(prefix=".cloudflared-credential.", dir=destination.parent)
temporary = Path(temporary_name)
try:
    os.fchmod(fd, 0o600)
    os.fchown(fd, int(os.environ["DEST_UID"]), int(os.environ["DEST_GID"]))
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, destination)
    parent_fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent_fd)
    finally:
        os.close(parent_fd)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
PY
  elif [[ ! -f "${system_credential}" ]] || [[ -L "${system_credential}" ]]; then
    err "Missing tunnel credential file for ${TUNNEL_UUID}."
    err "Expected ${user_credential} or ${system_credential}."
    err "If this tunnel was created on another machine, copy that JSON credential file here or choose a new tunnel name."
    return 1
  else
    sudo env \
      CREDENTIAL_FILE="${system_credential}" \
      EXPECTED_UID="${cloud_uid}" \
      EXPECTED_GID="${cloud_gid}" \
      EXPECTED_TUNNEL_UUID="${TUNNEL_UUID}" \
      python3 - <<'PY'
import json
import os
import stat

fd = os.open(os.environ["CREDENTIAL_FILE"], os.O_RDONLY | os.O_NOFOLLOW)
try:
    metadata = os.fstat(fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != int(os.environ["EXPECTED_UID"])
        or metadata.st_gid != int(os.environ["EXPECTED_GID"])
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size > 1024 * 1024
    ):
        raise OSError("unsafe installed Cloudflare credential")
    with os.fdopen(fd, encoding="utf-8") as handle:
        fd = -1
        payload = json.load(handle)
finally:
    if fd >= 0:
        os.close(fd)
if (
    not isinstance(payload, dict)
    or payload.get("TunnelID", "").lower()
    != os.environ["EXPECTED_TUNNEL_UUID"].lower()
):
    raise OSError("installed Cloudflare credential UUID mismatch")
PY
  fi
  sudo chown "${CLOUDFLARED_USER}:${CLOUDFLARED_USER}" \
    "${system_credential}"
  sudo chmod 600 "${system_credential}"

  # The account-management certificate remains in the invoking user's home.
  # The runtime connector needs only the narrowly copied tunnel credential.
}

write_cloudflare_config() {
  local tunnel_name="$1"
  local public_hostname="$2"
  local tunnel_ref="${TUNNEL_UUID:-$tunnel_name}"
  local temporary

  if ! is_tunnel_uuid "${TUNNEL_UUID:-}"; then
    err "Cannot write cloudflared config without a valid tunnel UUID."
    exit 1
  fi

  temporary="$(mktemp)"
  cat > "${temporary}" <<EOF
tunnel: ${tunnel_ref}
credentials-file: ${CLOUDFLARED_SYSTEM_DIR}/${TUNNEL_UUID}.json

ingress:
  - hostname: ${public_hostname}
    service: http://127.0.0.1:80
  - service: http_status:404
EOF

  sudo python3 - "${temporary}" "${CLOUDFLARED_CONFIG_FILE}" <<'PY'
import os
import sys
import tempfile
from pathlib import Path

source = Path(sys.argv[1])
target = Path(sys.argv[2])
if target.is_symlink() or (target.exists() and not target.is_file()):
    raise SystemExit("unsafe cloudflared config target")
payload = source.read_bytes()
fd, temporary_name = tempfile.mkstemp(prefix=".cloudflared-config.", dir=target.parent)
staged = Path(temporary_name)
try:
    os.fchmod(fd, 0o644)
    with os.fdopen(fd, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.chown(staged, 0, 0)
    os.replace(staged, target)
    directory_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    try:
        staged.unlink()
    except FileNotFoundError:
        pass
PY
  rm -f "${temporary}"
}

route_cloudflare_dns() {
  local tunnel_name="$1"
  local public_hostname="$2"
  local tunnel_ref="${TUNNEL_UUID:-$tunnel_name}"
  local route_output

  info "Creating Cloudflare DNS route for ${public_hostname}."
  if route_output="$(cloudflared_with_cert tunnel route dns "${tunnel_ref}" "${public_hostname}" 2>&1)"; then
    printf '%s\n' "${route_output}"
    return
  fi

  printf '%s\n' "${route_output}"
  err "Cloudflare DNS route could not be created for ${public_hostname}."
  err "No DNS record was overwritten. Review the existing record and Cloudflare permissions in the dashboard."
  return 1
}

install_cloudflared_service() {
  local service_file="/etc/systemd/system/${CLOUDFLARED_SERVICE_NAME}.service"
  local service_source
  if [[ ! -f "${CLOUDFLARED_BIN}" ]] || [[ -L "${CLOUDFLARED_BIN}" ]] \
    || [[ ! -x "${CLOUDFLARED_BIN}" ]] \
    || [[ "$(stat -c '%U:%G' "${CLOUDFLARED_BIN}" 2>/dev/null || true)" != "root:root" ]] \
    || [[ -n "$(find "${CLOUDFLARED_BIN}" -maxdepth 0 -perm /022 -print -quit 2>/dev/null)" ]]; then
    err "Refusing untrusted cloudflared executable: ${CLOUDFLARED_BIN}"
    return 1
  fi

  ensure_locked_system_user "${CLOUDFLARED_USER}" || return 1
  stop_conflicting_cloudflared_services || return 1

  service_source="$(mktemp)"
  chmod 600 "${service_source}" || {
    rm -f "${service_source}"
    return 1
  }
  if ! tee "${service_source}" >/dev/null <<EOF
[Unit]
Description=cloudflared tunnel for ${SERVICE_NAME}
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=10min
StartLimitBurst=5

[Service]
Type=notify
User=${CLOUDFLARED_USER}
Group=${CLOUDFLARED_USER}
ExecStart=${CLOUDFLARED_BIN} --no-autoupdate --config ${CLOUDFLARED_CONFIG_FILE} tunnel run
Restart=on-failure
RestartSec=5s
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
CapabilityBoundingSet=
AmbientCapabilities=
LockPersonality=true

[Install]
WantedBy=multi-user.target
EOF
  then
    rm -f "${service_source}"
    err "Could not stage the Cloudflare systemd unit."
    return 1
  fi

  if ! atomic_install_root_file "${service_source}" "${service_file}" 0644; then
    rm -f "${service_source}"
    err "Could not safely install the Cloudflare systemd unit."
    return 1
  fi
  rm -f "${service_source}"
  if ! sudo systemctl daemon-reload; then
    err "Could not reload systemd after installing the Cloudflare unit."
    return 1
  fi
  if ! disable_systemd_unit_if_present "${CLOUDFLARED_SERVICE_NAME}.service"; then
    err "Could not place the Cloudflare service in a disabled probe state."
    return 1
  fi
  verify_systemd_unit_install \
    "${CLOUDFLARED_SERVICE_NAME}.service" "${service_file}" "disabled" || return 1
  sudo systemctl reset-failed "${CLOUDFLARED_SERVICE_NAME}" 2>/dev/null || true
  CLOUDFLARED_PROBE_ACTIVE=1
  sudo systemctl restart "${CLOUDFLARED_SERVICE_NAME}"

  sleep 2
  if ! verify_systemd_unit_state \
    "${CLOUDFLARED_SERVICE_NAME}.service" "loaded" "active" "disabled"; then
    err "${CLOUDFLARED_SERVICE_NAME}.service did not stay running."
    err "Check: sudo journalctl -u ${CLOUDFLARED_SERVICE_NAME} -n 50"
    return 1
  fi
}

verify_public_https_redirect() {
  local hostname="$1" request_path="${2:-/}" headers status location remaining=12
  [[ "${request_path}" == "/" || "${request_path}" == "/api/readiness" ]] || return 1

  while (( remaining > 0 )); do
    headers="$(
      "${CURL_BIN}" -q --noproxy '*' --proto '=http' \
        --connect-timeout 2 --max-time 3 --max-redirs 0 -sS \
        -D - -o /dev/null "http://${hostname}${request_path}" 2>/dev/null || true
    )"
    status="$(
      printf '%s\n' "${headers}" \
        | tr -d '\r' \
        | awk 'substr(toupper($1), 1, 5) == "HTTP/" { print $2; exit }'
    )"
    location="$(
      printf '%s\n' "${headers}" \
        | tr -d '\r' \
        | awk 'tolower($1) == "location:" { sub(/^[^:]*:[[:space:]]*/, ""); print; exit }'
    )"
    if [[ "${status}" =~ ^30[1278]$ ]] \
      && python3 - "${location}" "${hostname}" <<'PY'
import sys
from urllib.parse import urlsplit

raw_url, expected_hostname = sys.argv[1:]
try:
    parsed = urlsplit(raw_url)
    port = parsed.port
except ValueError:
    raise SystemExit(1)
valid = (
    raw_url == raw_url.strip()
    and parsed.scheme == "https"
    and parsed.hostname == expected_hostname
    and port in (None, 443)
    and parsed.username is None
    and parsed.password is None
)
raise SystemExit(0 if valid else 1)
PY
    then
      return 0
    fi
    sleep 2
    remaining=$((remaining - 1))
  done

  err "Public HTTP ${request_path} did not redirect to https://${hostname} within 60 seconds."
  return 1
}

verify_public_https_access() {
  local hostname="$1" request_path="${2:-/}" headers status location remaining=12
  [[ "${request_path}" == "/" || "${request_path}" == "/api/readiness" ]] || return 1

  while (( remaining > 0 )); do
    headers="$(
      "${CURL_BIN}" -q --noproxy '*' --proto '=https' --tlsv1.2 \
        --connect-timeout 2 --max-time 3 --max-redirs 0 -sS \
        -D - -o /dev/null "https://${hostname}${request_path}" 2>/dev/null || true
    )"
    status="$(
      printf '%s\n' "${headers}" \
        | tr -d '\r' \
        | awk 'substr(toupper($1), 1, 5) == "HTTP/" { print $2; exit }'
    )"
    location="$(
      printf '%s\n' "${headers}" \
        | tr -d '\r' \
        | awk 'tolower($1) == "location:" { sub(/^[^:]*:[[:space:]]*/, ""); print; exit }'
    )"
    if python3 - "${status}" "${location}" "${headers}" <<'PY'
import re
import sys
from urllib.parse import urlsplit

status, location, headers = sys.argv[1:]

def is_access_url(raw_url: str) -> bool:
    try:
        parsed = urlsplit(raw_url)
        port = parsed.port
    except ValueError:
        return False
    hostname = (parsed.hostname or "").lower()
    return (
        raw_url == raw_url.strip()
        and parsed.scheme == "https"
        and hostname.endswith(".cloudflareaccess.com")
        and hostname != "cloudflareaccess.com"
        and port in (None, 443)
        and parsed.username is None
        and parsed.password is None
    )

if re.fullmatch(r"30[1278]", status) and is_access_url(location):
    raise SystemExit(0)
if status == "401":
    for line in headers.replace("\r", "").splitlines():
        if line.lower().startswith("www-authenticate:"):
            urls = re.findall(r'https://[^\s,"<>]+', line)
            if any(is_access_url(url.rstrip(")];")) for url in urls):
                raise SystemExit(0)
raise SystemExit(1)
PY
    then
      return 0
    fi
    sleep 2
    remaining=$((remaining - 1))
  done

  err "HTTPS certificate/Cloudflare Access validation failed for https://${hostname}${request_path} within 60 seconds."
  return 1
}

setup_cloudflare() {
  banner "Cloudflare Tunnel"

  local public_hostname

  public_hostname="$(prompt_with_default "Cloudflare hostname to route to this Pi, for example uconn.example.com" "${PUBLIC_HOSTNAME:-}")"
  public_hostname="$(normalize_hostname_input "${public_hostname}")"

  if [[ -z "${public_hostname}" ]]; then
    err "A public hostname is required for Cloudflare tunnel routing."
    exit 1
  fi

  if ! is_valid_dns_hostname "${public_hostname}"; then
    err "Cloudflare hostname must be a DNS hostname without a path or port; got ${public_hostname}."
    exit 1
  fi

  warn "Before this tunnel starts, enable Cloudflare 'Always Use HTTPS' and create an Access application and allow policy for https://${public_hostname}."
  warn "The app token is necessary, but it is not a substitute for an internet-facing identity policy."
  if ! confirm_default_no "Is Cloudflare Access already enforced for this exact hostname?"; then
    apply_local_only_hosting
    info "Public hosting is disabled. Configure Cloudflare Access, then rerun the installer."
    return 0
  fi
  if ! confirm_default_no "Is Cloudflare 'Always Use HTTPS' enabled for this hostname's zone?"; then
    apply_local_only_hosting
    info "Public hosting is disabled. Enable 'Always Use HTTPS', then rerun the installer."
    return 0
  fi

  PUBLIC_HOSTNAME="${public_hostname}"

  install_cloudflared
  ensure_locked_system_user "${CLOUDFLARED_USER}"
  ensure_cloudflare_authenticated
  select_cloudflare_tunnel_name
  ensure_cloudflare_tunnel "${TUNNEL_NAME}"
  enter_deployment_maintenance "cloudflare_setup" || return 1
  MAINTENANCE_RECOVERY_FAILED=1
  sleep 1
  if (( $(active_workflow_count) != 0 )); then
    MAINTENANCE_RECOVERY_FAILED=0
    leave_deployment_maintenance
    err "Cloudflare setup requires zero active workflows before the backend configuration restart."
    return 1
  fi
  if ! disable_systemd_unit_if_present "${CLOUDFLARED_SERVICE_NAME}.service"; then
    MAINTENANCE_RECOVERY_FAILED=1
    err "Could not disable the prior Cloudflare service before reconfiguration."
    return 1
  fi
  stop_conflicting_cloudflared_services
  copy_cloudflare_credentials
  write_cloudflare_config "${TUNNEL_NAME}" "${PUBLIC_HOSTNAME}"
  route_cloudflare_dns "${TUNNEL_NAME}" "${PUBLIC_HOSTNAME}"
  install_backend_service
  install_nginx_config
  install_cloudflared_service
  if ! verify_public_https_redirect "${PUBLIC_HOSTNAME}" "/" \
    || ! verify_public_https_access "${PUBLIC_HOSTNAME}" "/" \
    || ! verify_public_https_redirect "${PUBLIC_HOSTNAME}" "/api/readiness" \
    || ! verify_public_https_access "${PUBLIC_HOSTNAME}" "/api/readiness"; then
    if ! disable_systemd_unit_if_present "${CLOUDFLARED_SERVICE_NAME}.service"; then
      MAINTENANCE_RECOVERY_FAILED=1
      err "Cloudflare probe failed and the tunnel could not be proven disabled; maintenance remains enabled."
      return 1
    fi
    CLOUDFLARED_PROBE_ACTIVE=0
    unset PUBLIC_HOSTNAME TUNNEL_NAME TUNNEL_UUID
    install_backend_service
    install_nginx_config
    save_deploy_config
    MAINTENANCE_RECOVERY_FAILED=0
    leave_deployment_maintenance
    err "Public hosting remains disabled. Cloudflare DNS/tunnel records were left for review."
    return 1
  fi
  save_deploy_config
  sudo systemctl enable "${CLOUDFLARED_SERVICE_NAME}"
  if ! verify_systemd_unit_state \
    "${CLOUDFLARED_SERVICE_NAME}.service" "loaded" "active" "enabled"; then
    err "Managed Cloudflare service did not remain enabled."
    return 1
  fi
  CLOUDFLARED_PROBE_ACTIVE=0
  MAINTENANCE_RECOVERY_FAILED=0
  leave_deployment_maintenance

  info "Cloudflare tunnel is configured for https://${PUBLIC_HOSTNAME}"
}

disable_cloudflare_public_hosting() {
  disable_systemd_unit_if_present "${CLOUDFLARED_SERVICE_NAME}.service"
  stop_conflicting_cloudflared_services
  info "Managed Cloudflare tunnel service is disabled. Remote tunnel/DNS records were left intact for deliberate reuse or removal."
  unset PUBLIC_HOSTNAME TUNNEL_NAME TUNNEL_UUID
}

apply_local_only_hosting() {
  enter_deployment_maintenance "local_only" || return 1
  MAINTENANCE_RECOVERY_FAILED=1
  sleep 1
  if (( $(active_workflow_count) != 0 )); then
    MAINTENANCE_RECOVERY_FAILED=0
    leave_deployment_maintenance
    err "Local-only reconfiguration requires zero active workflows."
    return 1
  fi
  disable_cloudflare_public_hosting
  install_backend_service
  install_nginx_config
  save_deploy_config
  MAINTENANCE_RECOVERY_FAILED=0
  leave_deployment_maintenance
}

write_deployment_status() {
  # Arguments: state stage attempted active previous rollback deferred workflows message
  if sudo test -L "${DEPLOY_STATUS_DIR}" \
    || { sudo test -e "${DEPLOY_STATUS_DIR}" && ! sudo test -d "${DEPLOY_STATUS_DIR}"; }; then
    err "Refusing unsafe deployment status directory: ${DEPLOY_STATUS_DIR}"
    return 1
  fi
  sudo install -d -o root -g root -m 755 "${DEPLOY_STATUS_DIR}"
  sudo env DEPLOY_STATUS_FILE="${DEPLOY_STATUS_FILE}" DEPLOY_STATUS_LINK="${DEPLOY_STATUS_LINK}" python3 - "$@" <<'PY'
import json
import os
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

keys = ("status", "stage", "attempted_commit", "active_commit", "previous_commit", "rollback_succeeded", "deferred", "active_workflows", "message")
values = dict(zip(keys, sys.argv[1:], strict=False))
path = Path(os.environ["DEPLOY_STATUS_FILE"])
link = Path(os.environ["DEPLOY_STATUS_LINK"])
try:
    old_fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        old_stat = os.fstat(old_fd)
        if not stat.S_ISREG(old_stat.st_mode) or old_stat.st_uid != 0:
            raise OSError("unsafe status file")
        with os.fdopen(old_fd, encoding="utf-8") as old_handle:
            old_fd = -1
            old = json.load(old_handle)
    finally:
        if old_fd >= 0:
            os.close(old_fd)
except (OSError, ValueError, json.JSONDecodeError):
    old = {}
events = old.get("events", []) if isinstance(old, dict) and isinstance(old.get("events"), list) else []
now = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
rollback = values["rollback_succeeded"].lower()
failed_commit = old.get("failed_commit", "") if isinstance(old, dict) else ""
if (
    values["status"] in {"failed", "rolled_back"}
    and values["stage"] in {"build", "health", "rollback"}
    and values["attempted_commit"]
):
    failed_commit = values["attempted_commit"]
elif (
    values["status"] == "success"
    and values["stage"] != "manual_rollback"
    and values["attempted_commit"]
):
    failed_commit = ""
payload = {
    "status": values["status"], "stage": values["stage"],
    "attempted_commit": values["attempted_commit"], "active_commit": values["active_commit"],
    "previous_commit": values["previous_commit"],
    "failed_commit": failed_commit,
    "rollback_succeeded": True if rollback == "true" else False if rollback == "false" else None,
    "deferred": values["deferred"].lower() == "true",
    "active_workflows": (
        int(values["active_workflows"]) if values["active_workflows"].isdigit() else None
    ),
    "workflow_count_known": values["active_workflows"].isdigit(),
    "updated_at": now,
}
events.append({"at": now, "state": values["status"], "stage": values["stage"],
               "commit": values["attempted_commit"], "message": values["message"][:400]})
payload["events"] = events[-50:]
fd, temporary_name = tempfile.mkstemp(prefix=".deployment-status.", dir=path.parent)
temporary = Path(temporary_name)
try:
    os.fchmod(fd, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass

# The backend reads a stable link from its writable runtime directory. Replace
# only that directory entry; never write the root-owned status through it.
link_parent_stat = link.parent.lstat()
if not stat.S_ISDIR(link_parent_stat.st_mode) or stat.S_ISLNK(link_parent_stat.st_mode):
    raise OSError("unsafe runtime status-link parent")
link_temp = link.parent / f".deployment-status-link.{os.getpid()}.{os.urandom(8).hex()}"
try:
    os.symlink(path, link_temp)
    os.replace(link_temp, link)
finally:
    try:
        link_temp.unlink()
    except FileNotFoundError:
        pass
PY
}

current_release_path() {
  readlink -f "${CURRENT_RELEASE}" 2>/dev/null || true
}

current_release_commit() {
  local current
  current="$(current_release_path)"
  [[ -n "${current}" ]] && basename "${current}" || true
}

release_is_verified_direct() {
  local release_dir="$1" commit_sha="$2"

  [[ "${commit_sha}" =~ ^[0-9a-f]{40}$ ]] \
    && [[ "${release_dir}" == "${RELEASES_DIR}/${commit_sha}" ]] \
    && release_is_complete "${release_dir}" "${commit_sha}"
}

selected_release_is_verified() {
  local current current_sha

  [[ -L "${CURRENT_RELEASE}" ]] || return 1
  current="$(current_release_path)"
  current_sha="$(current_release_commit)"
  [[ -n "${current}" ]] && release_is_verified_direct "${current}" "${current_sha}"
}

active_workflow_count() {
  local payload load_state active_state
  if ! load_state="$(systemd_unit_property "${SERVICE_NAME}.service" "LoadState")"; then
    printf '%s\n' '-1'
    return 0
  fi
  if [[ "${load_state}" == "not-found" ]]; then
    if [[ -n "$(backend_port_pids)" ]]; then
      printf '%s\n' '-1'
    else
      printf '%s\n' '0'
    fi
    return 0
  fi
  if ! active_state="$(systemd_unit_property "${SERVICE_NAME}.service" "ActiveState")"; then
    printf '%s\n' '-1'
    return 0
  fi
  if [[ "${active_state}" != "active" ]]; then
    # Do not send the access token to an unverified replacement listener. An
    # inactive unit is idle only when no process is listening on the app port.
    if [[ "${active_state}" != "inactive" && "${active_state}" != "failed" ]] \
      || [[ -n "$(backend_port_pids)" ]]; then
      printf '%s\n' '-1'
    else
      printf '%s\n' '0'
    fi
    return 0
  fi
  payload="$(
    sudo "${RUNTIME_ENV_HELPER}" authenticated-get \
      "${RUNTIME_ENV_FILE}" \
      "APP_ACCESS_TOKEN" \
      "http://127.0.0.1:${BACKEND_PORT}/api/deployment/status" 2>/dev/null || true
  )"
  python3 -c '
import json
import sys

payload = json.load(sys.stdin)
if not isinstance(payload, dict) or "active_workflows" not in payload:
    raise SystemExit(1)
value = payload["active_workflows"]
if type(value) is not int or value < 0:
    raise SystemExit(1)
print(value)
' <<< "${payload}" 2>/dev/null || printf '%s\n' '-1'
}

link_persistent_data() {
  local release_dir="$1"
  local owner="${2:-root}"
  local name
  if [[ ! -d "${release_dir}/site/backend" ]] || [[ -L "${release_dir}/site/backend" ]]; then
    err "Candidate release has an unsafe backend directory."
    return 1
  fi
  for name in uploads outputs runtime; do
    sudo -u "${owner}" rm -rf "${release_dir}/site/backend/${name}"
    sudo -u "${owner}" ln -s "${PERSIST_DIR}/${name}" "${release_dir}/site/backend/${name}"
  done
}

ensure_release_layout() {
  local name legacy_dir current legacy_sha ownership_marker ownership_expected ownership_actual
  ensure_service_users
  for name in "${DEPLOY_DIR}" "${PERSIST_DIR}"; do
    if sudo test -L "${name}" || { sudo test -e "${name}" && ! sudo test -d "${name}"; }; then
      err "Managed root is not a real directory: ${name}"
      return 1
    fi
  done
  sudo install -d -o root -g root -m 755 "${DEPLOY_DIR}" "${PERSIST_DIR}"
  if sudo test -L "${RELEASES_DIR}" \
    || { sudo test -e "${RELEASES_DIR}" && ! sudo test -d "${RELEASES_DIR}"; }; then
    err "Release root is not a real directory: ${RELEASES_DIR}"
    return 1
  fi
  sudo install -d -o root -g root -m 755 "${RELEASES_DIR}"
  for name in uploads outputs runtime config; do
    if sudo test -L "${PERSIST_DIR}/${name}" \
      || { sudo test -e "${PERSIST_DIR}/${name}" && ! sudo test -d "${PERSIST_DIR}/${name}"; }; then
      err "Persistent path is not a real directory: ${PERSIST_DIR}/${name}"
      return 1
    fi
    sudo install -d -o "${APP_USER}" -g "${APP_USER}" -m 750 "${PERSIST_DIR}/${name}"
  done
  # Persistent dashboard override file; systemd reads it as root before the
  # service drops to APP_USER, so it stays owner-only.
  if sudo test -L "${RUNTIME_CONFIG_FILE}" \
    || { sudo test -e "${RUNTIME_CONFIG_FILE}" && ! sudo test -f "${RUNTIME_CONFIG_FILE}"; }; then
    err "Refusing unsafe dashboard runtime config: ${RUNTIME_CONFIG_FILE}"
    return 1
  fi
  if ! sudo test -e "${RUNTIME_CONFIG_FILE}"; then
    sudo install -o "${APP_USER}" -g "${APP_USER}" -m 600 /dev/null "${RUNTIME_CONFIG_FILE}"
  else
    sudo chown "${APP_USER}:${APP_USER}" "${RUNTIME_CONFIG_FILE}"
    sudo chmod 600 "${RUNTIME_CONFIG_FILE}"
  fi
  if sudo test -L "${DEPLOY_STATUS_DIR}" \
    || { sudo test -e "${DEPLOY_STATUS_DIR}" && ! sudo test -d "${DEPLOY_STATUS_DIR}"; }; then
    err "Refusing unsafe deployment status directory: ${DEPLOY_STATUS_DIR}"
    return 1
  fi
  sudo install -d -o root -g root -m 755 "${DEPLOY_STATUS_DIR}"
  ownership_marker="${PERSIST_DIR}/.ownership-uconn-v1"
  ownership_expected="$(printf 'schema=1\napp_uid=%s\napp_gid=%s' \
    "$(id -u "${APP_USER}")" "$(id -g "${APP_USER}")")"
  ownership_actual=""
  if [[ -f "${ownership_marker}" ]] && [[ ! -L "${ownership_marker}" ]] \
    && [[ "$(sudo stat -c '%U:%G:%a' "${ownership_marker}" 2>/dev/null || true)" == "root:root:444" ]]; then
    ownership_actual="$(sudo sed -n '1,3p' "${ownership_marker}" 2>/dev/null || true)"
  fi
  if [[ "${ownership_actual}" != "${ownership_expected}" ]]; then
    if systemctl is-active --quiet "${SERVICE_NAME}"; then
      enter_deployment_maintenance "ownership_migration" || return 1
      sleep 1
      if (( $(active_workflow_count) != 0 )); then
        leave_deployment_maintenance
        err "Persistent ownership migration requires zero active workflows."
        return 1
      fi
      sudo systemctl stop "${SERVICE_NAME}"
      MIGRATION_STOPPED_SERVICE=1
      if [[ -n "$(backend_port_pids)" ]]; then
        err "The managed backend stopped but port ${BACKEND_PORT} still has a listener; ownership migration refused."
        return 1
      fi
    elif [[ -n "$(backend_port_pids)" ]]; then
      err "The backend unit is inactive but port ${BACKEND_PORT} has a listener; ownership migration refused."
      return 1
    fi
    sudo find -P "${PERSIST_DIR}/uploads" "${PERSIST_DIR}/outputs" "${PERSIST_RUNTIME_DIR}" \
      -xdev \( ! -user "${APP_USER}" -o ! -group "${APP_USER}" \) \
      -exec chown -h "${APP_USER}:${APP_USER}" {} +
    if sudo test -e "${ownership_marker}" || sudo test -L "${ownership_marker}"; then
      sudo rm -f -- "${ownership_marker}"
    fi
    if sudo test -e "${ownership_marker}" || sudo test -L "${ownership_marker}"; then
      err "Refusing unsafe persistent ownership marker: ${ownership_marker}"
      return 1
    fi
    sudo install -o root -g root -m 600 /dev/null "${ownership_marker}"
    printf '%s\n' "${ownership_expected}" | sudo tee "${ownership_marker}" >/dev/null
    sudo chmod 444 "${ownership_marker}"
  fi
  if sudo test -e "${CURRENT_RELEASE}" || sudo test -L "${CURRENT_RELEASE}"; then
    if ! sudo test -L "${CURRENT_RELEASE}"; then
      err "Current release selector is not a symlink: ${CURRENT_RELEASE}"
      return 1
    fi
    current="$(current_release_path)"
    legacy_sha="$(basename "${current}")"
    if [[ "${current}" == "${RELEASES_DIR}/${legacy_sha}" ]] \
      && [[ "${legacy_sha}" =~ ^[0-9a-f]{40}$ ]] \
      && release_is_complete "${current}" "${legacy_sha}"; then
      return 0
    fi
    enter_deployment_maintenance "ownership_migration" || return 1
    MAINTENANCE_RECOVERY_FAILED=1
    stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
    err "Current release selector is outside the verified release set; the backend is stopped."
    err "Review and remove only the selector ${CURRENT_RELEASE}, then rerun the installer."
    return 1
  fi

  # One-time migration from the legacy mutable directory. Copy first so a
  # failed migration cannot destroy user content.
  if [[ -L "${DEPLOY_DIR}/site" ]] || [[ -L "${DEPLOY_DIR}/site/backend" ]]; then
    err "Refusing symlinked legacy deployment directories."
    return 1
  fi
  for name in uploads outputs runtime; do
    legacy_dir="${DEPLOY_DIR}/site/backend/${name}"
    if [[ -L "${legacy_dir}" ]]; then
      err "Refusing symlinked legacy persistent directory: ${legacy_dir}"
      return 1
    fi
    if [[ -d "${legacy_dir}" ]]; then
      if [[ -n "$(sudo find -P "${legacy_dir}" -xdev \( -type l -o ! -type f ! -type d \) -print -quit)" ]]; then
        err "Legacy persistent data contains symlinks or special files; review it manually before migration."
        return 1
      fi
      sudo rsync -a --chown="${APP_USER}:${APP_USER}" --ignore-existing \
        "${legacy_dir}/" "${PERSIST_DIR}/${name}/"
    fi
  done

  if [[ ! -d "${DEPLOY_DIR}/site" ]]; then
    return 0
  fi

  legacy_sha="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("commit", ""))' "${DEPLOY_DIR}/site/dist/version.json" 2>/dev/null || true)"
  if ! [[ "${legacy_sha}" =~ ^[0-9a-f]{40}$ ]]; then
    legacy_sha="$(git -C "${REPO_DIR}" rev-parse HEAD 2>/dev/null || true)"
  fi
  [[ "${legacy_sha}" =~ ^[0-9a-f]{40}$ ]] || legacy_sha="legacy-$(date -u +%Y%m%d%H%M%S)"
  current="${RELEASES_DIR}/${legacy_sha}"
  if [[ ! -d "${current}" ]]; then
    sudo mkdir -p "${current}"
    sudo rsync -a --exclude "releases/" --exclude "current" --exclude "persistent/" "${DEPLOY_DIR}/" "${current}/"
  fi
  link_persistent_data "${current}"
  harden_release_ownership "${current}"
  switch_current_release "${current}"
}

switch_current_release() {
  local release_dir next_link
  release_dir="$(readlink -f "$1")"
  if [[ ! -d "${release_dir}" ]] || [[ "${release_dir}" != "${RELEASES_DIR}/"* ]]; then
    err "Refusing to activate invalid release path: $1"
    return 1
  fi
  next_link="${DEPLOY_DIR}/.current-next-$$"
  sudo ln -s "${release_dir}" "${next_link}"
  sudo mv -Tf "${next_link}" "${CURRENT_RELEASE}"
  sudo python3 - "${DEPLOY_DIR}" <<'PY'
import os
import sys

directory_fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)
try:
    os.fsync(directory_fd)
finally:
    os.close(directory_fd)
PY
}

write_release_version() {
  local release_dir="$1" commit_sha="$2" commit_time="$3" owner="$4"
  sudo -u "${owner}" env RELEASE_DIR="${release_dir}" COMMIT_SHA="${commit_sha}" COMMIT_TIME="${commit_time}" python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

path = Path(os.environ["RELEASE_DIR"]) / "site" / "dist" / "version.json"
path.write_text(json.dumps({
    "version": os.environ["COMMIT_SHA"][:7],
    "commit": os.environ["COMMIT_SHA"],
    "commit_time": os.environ["COMMIT_TIME"],
    "published_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
}, indent=2) + "\n", encoding="utf-8")
PY
}

safe_file_sha256() {
  python3 - "$1" <<'PY'
import hashlib
import os
import stat
import sys

path = sys.argv[1]
fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
try:
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        raise SystemExit(1)
    digest = hashlib.sha256()
    while chunk := os.read(fd, 1024 * 1024):
        digest.update(chunk)
    print(digest.hexdigest())
finally:
    os.close(fd)
PY
}

immutable_release_digest() {
  python3 - "$1" <<'PY'
import hashlib
import os
import stat
import sys
from pathlib import Path

root = Path(sys.argv[1])
digest = hashlib.sha256()

def add(value: str | bytes) -> None:
    encoded = value.encode("utf-8", "surrogateescape") if isinstance(value, str) else value
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)

def visit(path: Path, relative: str) -> None:
    metadata = path.lstat()
    mode = stat.S_IMODE(metadata.st_mode)
    add(relative)
    add(f"{stat.S_IFMT(metadata.st_mode):o}:{mode:o}")
    if relative == ".venv":
        add("excluded-venv")
    elif stat.S_ISDIR(metadata.st_mode):
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            child_relative = child.name if not relative else f"{relative}/{child.name}"
            if child_relative == ".release-ready":
                continue
            visit(child, child_relative)
    elif stat.S_ISREG(metadata.st_mode):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError("file type changed during release hashing")
            while chunk := os.read(fd, 1024 * 1024):
                digest.update(chunk)
        finally:
            os.close(fd)
    elif stat.S_ISLNK(metadata.st_mode):
        add(os.readlink(path))
    else:
        raise OSError(f"unsupported file type in release: {relative}")

visit(root, "")
print(digest.hexdigest())
PY
}

application_source_digest() {
  sudo python3 - "$1" <<'PY'
import hashlib
import os
import stat
import sys
from pathlib import Path

release = Path(sys.argv[1])
excluded = {
    ".release-ready",
    ".venv",
    ".build-home",
    "site/dist",
    "site/frontend/node_modules",
    "site/backend/uploads",
    "site/backend/outputs",
    "site/backend/runtime",
}
digest = hashlib.sha256()

def add(value: str | bytes) -> None:
    encoded = value.encode("utf-8", "surrogateescape") if isinstance(value, str) else value
    digest.update(len(encoded).to_bytes(8, "big"))
    digest.update(encoded)

def visit(path: Path) -> None:
    relative = path.relative_to(release).as_posix()
    if relative in excluded:
        return
    metadata = path.lstat()
    add(relative)
    add(f"{stat.S_IFMT(metadata.st_mode):o}:{stat.S_IMODE(metadata.st_mode):o}")
    if stat.S_ISDIR(metadata.st_mode):
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            visit(child)
    elif stat.S_ISREG(metadata.st_mode):
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            while chunk := os.read(fd, 1024 * 1024):
                digest.update(chunk)
        finally:
            os.close(fd)
    elif stat.S_ISLNK(metadata.st_mode):
        add(os.readlink(path))
    else:
        raise OSError(f"unsupported source file type: {relative}")

visit(release)
print(digest.hexdigest())
PY
}

release_runtime_tree_is_safe() {
  sudo python3 - "$1" "${PERSIST_DIR}" <<'PY'
import os
import stat
import sys
from pathlib import Path

release = Path(sys.argv[1])
persist = Path(sys.argv[2])
runtime_roots = (
    release / ".venv",
    release / "site" / "dist",
    release / "site" / "backend",
)
allowed_links = {
    release / "site" / "backend" / name: persist / name
    for name in ("uploads", "outputs", "runtime")
}

for root in runtime_roots:
    if root.is_symlink() or not root.is_dir():
        raise SystemExit(1)

def visit(path: Path) -> None:
    metadata = path.lstat()
    if stat.S_ISLNK(metadata.st_mode):
        expected = allowed_links.get(path)
        if expected is None or path.resolve(strict=True) != expected.resolve(strict=True):
            raise SystemExit(1)
        return
    if stat.S_ISDIR(metadata.st_mode):
        for child in path.iterdir():
            visit(child)
        return
    if not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(1)

for root in runtime_roots:
    visit(root)

for relative in (
    ".venv/bin/uvicorn",
    "site/dist/index.html",
    "site/dist/version.json",
    "site/backend/requirements.txt",
):
    target = release / relative
    metadata = target.lstat()
    if not stat.S_ISREG(metadata.st_mode):
        raise SystemExit(1)
PY
}

stop_build_user_processes() {
  local status
  if sudo pkill -TERM -u "${BUILD_USER}" 2>/dev/null; then
    :
  else
    status=$?
    if [[ "${status}" -ne 1 ]]; then
      err "Could not inspect/terminate build-account processes (pkill status ${status})."
      return 1
    fi
  fi
  sleep 1
  if sudo pkill -KILL -u "${BUILD_USER}" 2>/dev/null; then
    :
  else
    status=$?
    if [[ "${status}" -ne 1 ]]; then
      err "Could not inspect/terminate build-account processes (pkill status ${status})."
      return 1
    fi
  fi
  if sudo pgrep -u "${BUILD_USER}" >/dev/null 2>&1; then
    err "The dedicated build account still has live processes; refusing to activate its output."
    return 1
  else
    status=$?
    if [[ "${status}" -ne 1 ]]; then
      err "Could not verify the build account is idle (pgrep status ${status})."
      return 1
    fi
  fi
}

discard_candidate_release() {
  local release_dir="$1"
  if ! stop_build_user_processes; then
    err "Candidate cleanup is unsafe while build-account process state is unknown."
    return 1
  fi
  sudo rm -rf "${release_dir}"
}

release_marker_content() {
  local release_dir="$1" commit_sha="$2"
  local requirements_hash package_lock_hash immutable_hash
  requirements_hash="$(safe_file_sha256 "${release_dir}/site/backend/requirements.txt")" || return 1
  package_lock_hash="$(safe_file_sha256 "${release_dir}/site/frontend/package-lock.json")" || return 1
  immutable_hash="$(immutable_release_digest "${release_dir}")" || return 1
  printf 'schema=2\ncommit=%s\nrequirements_sha256=%s\npackage_lock_sha256=%s\nimmutable_sha256=%s\n' \
    "${commit_sha}" "${requirements_hash}" "${package_lock_hash}" "${immutable_hash}"
}

harden_release_ownership() {
  local release_dir="$1"
  if [[ ! -d "${release_dir}" ]] || [[ -L "${release_dir}" ]] \
    || [[ "${release_dir}" != "${RELEASES_DIR}/"* ]]; then
    err "Refusing to harden an invalid release path: ${release_dir}"
    return 1
  fi
  sudo find -P "${release_dir}" -xdev -exec chown -h root:root {} +
  sudo find -P "${release_dir}" -xdev ! -type l -exec chmod go-w {} +
  sudo chmod 755 "${release_dir}"
}

write_release_marker() {
  local release_dir="$1" marker_content="$2"
  sudo env RELEASE_DIR="${release_dir}" MARKER_CONTENT="${marker_content}" python3 - <<'PY'
import os
import tempfile
from pathlib import Path

release = Path(os.environ["RELEASE_DIR"])
marker = release / ".release-ready"
content = os.environ["MARKER_CONTENT"].rstrip("\n") + "\n"
fd, temporary_name = tempfile.mkstemp(prefix=".release-ready.", dir=release)
temporary = Path(temporary_name)
try:
    os.fchmod(fd, 0o444)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, marker)
    directory_fd = os.open(release, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
finally:
    try:
        temporary.unlink()
    except FileNotFoundError:
        pass
PY
}

release_is_complete() {
  local release_dir="$1" commit_sha="$2"
  local marker="${release_dir}/.release-ready"
  local name expected actual

  [[ -d "${release_dir}/.venv" ]] && [[ ! -L "${release_dir}/.venv" ]] || return 1
  [[ -d "${release_dir}/site/dist" ]] && [[ ! -L "${release_dir}/site/dist" ]] || return 1
  [[ -d "${release_dir}/site/backend" ]] && [[ ! -L "${release_dir}/site/backend" ]] || return 1
  [[ -x "${release_dir}/.venv/bin/uvicorn" ]] && [[ ! -L "${release_dir}/.venv/bin/uvicorn" ]] || return 1
  [[ -f "${release_dir}/site/dist/index.html" ]] || return 1
  [[ -f "${release_dir}/site/dist/version.json" ]] || return 1
  [[ -f "${release_dir}/site/backend/requirements.txt" ]] || return 1
  [[ -f "${release_dir}/site/frontend/package-lock.json" ]] || return 1
  [[ ! -L "${release_dir}" ]] || return 1
  [[ "$(stat -c '%U:%G' "${release_dir}" 2>/dev/null || true)" == "root:root" ]] || return 1
  release_runtime_tree_is_safe "${release_dir}" || return 1
  [[ -z "$(find -P "${release_dir}" -xdev ! -type l \
    \( ! -user root -o ! -group root -o -perm /022 \) -print -quit 2>/dev/null)" ]] || return 1
  [[ -f "${marker}" ]] && [[ ! -L "${marker}" ]] || return 1
  [[ "$(stat -c '%U:%G:%a' "${marker}" 2>/dev/null || true)" == "root:root:444" ]] || return 1
  python3 -c 'import json, sys; raise SystemExit(0 if json.load(open(sys.argv[1])).get("commit") == sys.argv[2] else 1)' \
    "${release_dir}/site/dist/version.json" "${commit_sha}" 2>/dev/null || return 1
  for name in uploads outputs runtime; do
    [[ -L "${release_dir}/site/backend/${name}" ]] || return 1
    [[ "$(readlink -f "${release_dir}/site/backend/${name}")" == "$(readlink -f "${PERSIST_DIR}/${name}")" ]] || return 1
  done
  expected="$(release_marker_content "${release_dir}" "${commit_sha}")" || return 1
  actual="$(<"${marker}")"
  [[ "${actual}" == "${expected}" ]]
}

build_candidate_release() {
  (
  umask 0022
  local commit_sha="$1" repo_owner="$2" commit_time release_dir marker_content
  local source_digest_before source_digest_after
  release_dir="${RELEASES_DIR}/${commit_sha}"
  [[ "${commit_sha}" =~ ^[0-9a-f]{40}$ ]] || return 1
  if release_is_complete "${release_dir}" "${commit_sha}"; then
    printf '%s\n' "${release_dir}"
    return 0
  fi
  # A venv's console scripts have an absolute interpreter path. Build directly
  # in the final, as-yet-unreferenced release directory rather than moving a
  # staging directory after venv creation.
  stop_build_user_processes || return 1
  sudo rm -rf "${release_dir}"
  commit_time="$(sudo -H -u "${repo_owner}" env GIT_NO_REPLACE_OBJECTS=1 \
    git -C "${REPO_DIR}" log -1 --format=%cI "${commit_sha}")"
  sudo install -d -o "${BUILD_USER}" -g "${BUILD_USER}" -m 750 "${release_dir}"
  if ! sudo -H -u "${repo_owner}" env GIT_NO_REPLACE_OBJECTS=1 \
    git -C "${REPO_DIR}" archive --format=tar "${commit_sha}" \
    | sudo -u "${BUILD_USER}" tar --no-same-owner --no-same-permissions -x -C "${release_dir}"; then
    discard_candidate_release "${release_dir}"
    return 1
  fi
  if [[ ! -f "${release_dir}/site/frontend/package-lock.json" ]] \
    || [[ -L "${release_dir}/site/frontend/package-lock.json" ]] \
    || [[ ! -f "${release_dir}/site/backend/requirements.txt" ]] \
    || [[ -L "${release_dir}/site/backend/requirements.txt" ]]; then
    err "Candidate ${commit_sha} has no frontend package-lock.json; refusing a non-reproducible npm install."
    discard_candidate_release "${release_dir}"
    return 1
  fi
  link_persistent_data "${release_dir}" "${BUILD_USER}"
  source_digest_before="$(application_source_digest "${release_dir}")" || {
    discard_candidate_release "${release_dir}"
    return 1
  }
  sudo -u "${BUILD_USER}" mkdir -p "${release_dir}/.build-home"
  if ! sudo -H -u "${BUILD_USER}" env HOME="${release_dir}/.build-home" \
      python3 -m venv --copies "${release_dir}/.venv"; then
    discard_candidate_release "${release_dir}"
    return 1
  fi
  # Some Python builds create lib64 -> lib even with --copies. The application
  # does not need that compatibility link, and production release trees permit
  # no runtime-code symlinks.
  sudo -u "${BUILD_USER}" rm -f "${release_dir}/.venv/lib64"
  if ! sudo -H -u "${BUILD_USER}" env HOME="${release_dir}/.build-home" \
      "${release_dir}/.venv/bin/pip" install --require-hashes --no-cache-dir --disable-pip-version-check \
      -r "${release_dir}/site/backend/requirements.txt" >&2; then
    discard_candidate_release "${release_dir}"
    return 1
  fi
  sudo -u "${BUILD_USER}" mkdir -p "${release_dir}/site/dist"
  if ! sudo -H -u "${BUILD_USER}" env HOME="${release_dir}/.build-home" \
      npm -C "${release_dir}/site/frontend" ci --no-audit --no-fund >&2 \
    || ! sudo -H -u "${BUILD_USER}" env HOME="${release_dir}/.build-home" \
      npm -C "${release_dir}/site/frontend" run build >&2 \
    || [[ ! -f "${release_dir}/site/dist/index.html" ]]; then
    discard_candidate_release "${release_dir}"
    return 1
  fi
  write_release_version "${release_dir}" "${commit_sha}" "${commit_time}" "${BUILD_USER}"
  sudo -u "${BUILD_USER}" rm -rf "${release_dir}/site/frontend/node_modules" "${release_dir}/.build-home"
  stop_build_user_processes || {
    discard_candidate_release "${release_dir}"
    return 1
  }
  source_digest_after="$(application_source_digest "${release_dir}")" || {
    discard_candidate_release "${release_dir}"
    return 1
  }
  if [[ "${source_digest_before}" != "${source_digest_after}" ]]; then
    err "A dependency build modified application source; refusing candidate ${commit_sha}."
    discard_candidate_release "${release_dir}"
    return 1
  fi
  release_runtime_tree_is_safe "${release_dir}" || {
    err "Candidate runtime contains a symlink or special-file escape."
    discard_candidate_release "${release_dir}"
    return 1
  }
  harden_release_ownership "${release_dir}"
  if ! sudo sync -f "${release_dir}"; then
    err "Could not durably flush candidate ${commit_sha} before activation."
    discard_candidate_release "${release_dir}"
    return 1
  fi
  marker_content="$(release_marker_content "${release_dir}" "${commit_sha}")" || {
    discard_candidate_release "${release_dir}"
    return 1
  }
  if ! write_release_marker "${release_dir}" "${marker_content}"; then
    discard_candidate_release "${release_dir}"
    return 1
  fi
  printf '%s\n' "${release_dir}"
  )
}

release_health_is_valid() {
  local expected_sha="$1" payload
  wait_for_backend_health || return 1
  payload="$(
    "${CURL_BIN}" -q --noproxy '*' --proto '=http' -fsS --max-time 5 \
      "http://127.0.0.1:${BACKEND_PORT}/version.json"
  )" || return 1
  python3 -c 'import json, sys; raise SystemExit(0 if json.load(sys.stdin).get("commit") == sys.argv[1] else 1)' "${expected_sha}" <<< "${payload}"
}

prune_old_releases() {
  local active previous=() release release_sha
  active="$(current_release_path)"
  mapfile -t previous < <(find "${RELEASES_DIR}" -mindepth 1 -maxdepth 1 -type d ! -name '.*' -printf '%T@ %p\n' | sort -rn | cut -d' ' -f2-)
  local kept=0
  for release in "${previous[@]}"; do
    [[ "${release}" == "${active}" ]] && continue
    release_sha="$(basename "${release}")"
    if ! [[ "${release_sha}" =~ ^[0-9a-f]{40}$ ]] \
      || ! release_is_complete "${release}" "${release_sha}"; then
      warn "Skipping unvalidated release during pruning: ${release}"
      continue
    fi
    if (( kept < RELEASE_RETENTION )); then
      ((kept += 1))
      continue
    fi
    sudo rm -rf "${release}"
  done
}

last_failed_commit() {
  python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("failed_commit", ""))' "${DEPLOY_STATUS_FILE}" 2>/dev/null || true
}

deploy_commit() {
  local commit_sha="$1" repo_owner="$2" defer_for_workflows="${3:-false}"
  local previous active release_dir workflow_count invalid_release quarantined_release=""
  local integrity_repair="false" previous_valid=0 corrupt_direct=0
  local backend_active_state=""
  if [[ "${INTEGRITY_REPAIR_ACTIVE}" -eq 1 ]]; then
    integrity_repair="true"
  fi
  previous="$(current_release_path)"
  active="$(current_release_commit)"
  if [[ -e "${CURRENT_RELEASE}" || -L "${CURRENT_RELEASE}" ]]; then
    if [[ -L "${CURRENT_RELEASE}" ]] \
      && [[ "${active}" =~ ^[0-9a-f]{40}$ ]] \
      && [[ "${previous}" == "${RELEASES_DIR}/${active}" ]] \
      && [[ -d "${previous}" ]] && [[ ! -L "${previous}" ]]; then
      if release_is_complete "${previous}" "${active}"; then
        previous_valid=1
      else
        corrupt_direct=1
      fi
    else
      enter_deployment_maintenance "activation" || return 1
      MAINTENANCE_RECOVERY_FAILED=1
      stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
      write_deployment_status "failed" "integrity_selector" "${commit_sha}" "${active}" "${active}" "" "false" "-1" "The current selector is not a verified direct release; the backend is stopped and maintenance remains enabled."
      return 1
    fi
  else
    previous=""
    active=""
  fi

  if [[ "${previous_valid}" -eq 1 && "${active}" == "${commit_sha}" ]]; then
    backend_active_state="$(
      systemd_unit_property "${SERVICE_NAME}.service" "ActiveState"
    )" || return 1
    if [[ "${backend_active_state}" == "active" ]] \
      && verify_systemd_unit_install \
        "${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service" "enabled" \
      && verify_systemd_unit_state \
        "${SERVICE_NAME}.service" "loaded" "active" "enabled" \
      && release_health_is_valid "${commit_sha}"; then
      if [[ "${INTEGRITY_REPAIR_ACTIVE}" -eq 1 ]]; then
        clear_integrity_repair_state || return 1
        INTEGRITY_REPAIR_ACTIVE=0
        MAINTENANCE_RECOVERY_FAILED=0
        leave_deployment_maintenance || return 1
      fi
      write_deployment_status "success" "up_to_date" "${commit_sha}" "${active}" "" "" "false" "0" "Requested commit is running, complete, and healthy."
      return 0
    fi
    if [[ "${backend_active_state}" != "active" ]] \
      && { [[ "${INTEGRITY_REPAIR_ACTIVE}" -ne 1 ]] \
        || sudo install -o root -g root -m 600 /dev/null \
          "${ACTIVATION_RECOVERY_ALLOW_FILE}"; } \
      && activate_backend_service "${defer_for_workflows}" \
      && release_health_is_valid "${commit_sha}"; then
      if [[ "${INTEGRITY_REPAIR_ACTIVE}" -eq 1 ]]; then
        sudo rm -f "${ACTIVATION_RECOVERY_ALLOW_FILE}"
      fi
      MIGRATION_STOPPED_SERVICE=0
      if [[ "${INTEGRITY_REPAIR_ACTIVE}" -eq 1 ]]; then
        clear_integrity_repair_state || return 1
        INTEGRITY_REPAIR_ACTIVE=0
      fi
      leave_deployment_maintenance
      write_deployment_status "success" "self_healed" "${commit_sha}" "${active}" "" "" "false" "0" "Requested commit was complete; an inactive backend was restarted and is healthy."
      return 0
    fi
    if [[ "${INTEGRITY_REPAIR_ACTIVE}" -eq 1 ]]; then
      sudo rm -f "${ACTIVATION_RECOVERY_ALLOW_FILE}"
    fi
    write_deployment_status "failed" "unhealthy" "${commit_sha}" "${active}" "" "" "false" "0" "Requested commit is complete but the active service or unit definition is unhealthy."
    return 1
  fi

  if [[ "${corrupt_direct}" -eq 1 ]]; then
    workflow_count="$(active_workflow_count)"
    if (( workflow_count != 0 )); then
      write_deployment_status "deferred" "integrity" "${commit_sha}" "${active}" "${active}" "" "true" "${workflow_count}" "Active release failed integrity validation; waiting for a confirmed idle service before rebuilding."
      return 0
    fi
    enter_deployment_maintenance "activation" || return 1
    sleep 1
    workflow_count="$(active_workflow_count)"
    if (( workflow_count != 0 )); then
      leave_deployment_maintenance
      write_deployment_status "deferred" "integrity" "${commit_sha}" "${active}" "${active}" "" "true" "${workflow_count}" "Active release failed integrity validation; waiting for a confirmed idle service before quarantining it."
      return 0
    fi
    PENDING_ACTIVATION=1
    if ! persist_activation_transaction \
      "" "" "${RELEASES_DIR}/${commit_sha}" "${commit_sha}" "${defer_for_workflows}"; then
      PENDING_ACTIVATION=0
      MAINTENANCE_RECOVERY_FAILED=1
      err "Could not persist integrity-repair recovery metadata; the release was not moved."
      return 1
    fi
    if ! persist_integrity_repair_state; then
      MAINTENANCE_RECOVERY_FAILED=1
      err "Could not persist the durable integrity-repair marker; the release was not moved."
      return 1
    fi
    stop_systemd_unit_if_present "${SERVICE_NAME}.service" || {
      MAINTENANCE_RECOVERY_FAILED=1
      return 1
    }
    invalid_release="${RELEASES_DIR}/.invalid-${active}-$(date -u +%Y%m%d%H%M%S)"
    if ! sudo mv -- "${previous}" "${invalid_release}" \
      || ! atomic_remove_root_path "${CURRENT_RELEASE}" "${DEPLOY_DIR}" "symlink" \
      || ! fsync_directory "${RELEASES_DIR}"; then
      MAINTENANCE_RECOVERY_FAILED=1
      err "Integrity quarantine did not complete durably; recovery metadata and maintenance remain active."
      return 1
    fi
    # Moving the direct release target makes the old current selector resolve
    # nowhere. Keep maintenance fail-closed throughout the rebuild instead of
    # pointing nginx back at a tree already known to have failed integrity.
    INTEGRITY_REPAIR_ACTIVE=1
    quarantined_release="${invalid_release}"
    previous=""
    integrity_repair="true"
  fi

  workflow_count="$(active_workflow_count)"
  if [[ -n "${previous}" ]] && (( workflow_count != 0 )); then
    write_deployment_status "deferred" "active_workflows" "${commit_sha}" "${active}" "${active}" "" "true" "${workflow_count}" "Waiting for a confirmed zero active-workflow count before restarting."
    return 0
  fi
  write_deployment_status "building" "candidate" "${commit_sha}" "${active}" "${active}" "" "false" "0" "Building isolated candidate release."
  if ! release_dir="$(build_candidate_release "${commit_sha}" "${repo_owner}")"; then
    if [[ "${integrity_repair}" == "true" ]]; then
      MAINTENANCE_RECOVERY_FAILED=1
      write_deployment_status "failed" "integrity_rebuild" "${commit_sha}" "" "${active}" "" "false" "0" "Integrity rebuild failed; the known-invalid release remains quarantined and the backend is stopped."
    else
      write_deployment_status "failed" "build" "${commit_sha}" "${active}" "${active}" "" "false" "0" "Candidate build failed; active release was not changed."
    fi
    return 1
  fi
  enter_deployment_maintenance "activation" || return 1
  sleep 1
  workflow_count="$(active_workflow_count)"
  if (( workflow_count != 0 )); then
    if [[ "${integrity_repair}" == "true" ]]; then
      MAINTENANCE_RECOVERY_FAILED=1
    else
      leave_deployment_maintenance
    fi
    write_deployment_status "deferred" "candidate_ready" "${commit_sha}" "${active}" "${active}" "" "true" "${workflow_count}" "Candidate is ready; activation waits for a confirmed zero workflow count."
    return 0
  fi
  if ! release_is_verified_direct "${release_dir}" "${commit_sha}"; then
    MAINTENANCE_RECOVERY_FAILED=1
    stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
    write_deployment_status "failed" "candidate_integrity" "${commit_sha}" "${active}" "${active}" "" "false" "0" "Candidate failed integrity verification immediately before activation."
    return 1
  fi
  if [[ -n "${previous}" ]] && ! release_is_verified_direct "${previous}" "${active}"; then
    MAINTENANCE_RECOVERY_FAILED=1
    stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
    write_deployment_status "failed" "rollback_integrity" "${commit_sha}" "${active}" "${active}" "" "false" "0" "The rollback release changed during the build; activation was refused."
    return 1
  fi
  write_deployment_status "activating" "switch" "${commit_sha}" "${active}" "${active}" "" "false" "0" "Switching the current release."
  PENDING_ACTIVATION=1
  if ! persist_activation_transaction \
    "${previous}" "${active}" "${release_dir}" "${commit_sha}" "${defer_for_workflows}"; then
    PENDING_ACTIVATION=0
    err "Could not persist activation recovery metadata; current release was not changed."
    return 1
  fi
  MAINTENANCE_RECOVERY_FAILED=0
  switch_current_release "${release_dir}" || return 1
  sudo install -o root -g root -m 600 /dev/null "${ACTIVATION_RECOVERY_ALLOW_FILE}"
  if activate_backend_service "${defer_for_workflows}" && release_health_is_valid "${commit_sha}"; then
    sudo rm -f "${ACTIVATION_RECOVERY_ALLOW_FILE}"
    clear_activation_transaction
    PENDING_ACTIVATION=0
    if [[ "${integrity_repair}" == "true" ]]; then
      clear_integrity_repair_state || return 1
      INTEGRITY_REPAIR_ACTIVE=0
    fi
    MIGRATION_STOPPED_SERVICE=0
    MAINTENANCE_RECOVERY_FAILED=0
    leave_deployment_maintenance
    if [[ -n "${quarantined_release}" ]] && [[ -d "${quarantined_release}" ]]; then
      sudo rm -rf "${quarantined_release}"
    fi
    prune_old_releases
    write_deployment_status "success" "healthy" "${commit_sha}" "${commit_sha}" "${active}" "" "false" "0" "Candidate is serving and passed health checks."
    return 0
  fi
  write_deployment_status "failed" "health" "${commit_sha}" "${commit_sha}" "${active}" "" "false" "0" "Candidate failed after activation; rolling back."
  if [[ -n "${previous}" ]] && release_is_verified_direct "${previous}" "${active}"; then
    switch_current_release "${previous}" || return 1
    if activate_backend_service "${defer_for_workflows}" && release_health_is_valid "${active}"; then
      sudo rm -f "${ACTIVATION_RECOVERY_ALLOW_FILE}"
      sudo rm -rf "${release_dir}"
      clear_activation_transaction
      PENDING_ACTIVATION=0
      MIGRATION_STOPPED_SERVICE=0
      leave_deployment_maintenance
      write_deployment_status "rolled_back" "rollback" "${commit_sha}" "${active}" "${active}" "true" "false" "0" "Rollback completed and the previous release is healthy."
    else
      sudo rm -f "${ACTIVATION_RECOVERY_ALLOW_FILE}"
      sudo systemctl stop "${SERVICE_NAME}" >/dev/null 2>&1 || true
      write_deployment_status "failed" "rollback" "${commit_sha}" "${active}" "${active}" "false" "false" "0" "Rollback could not restore a healthy service."
    fi
  else
    sudo rm -f "${ACTIVATION_RECOVERY_ALLOW_FILE}"
    stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
  fi
  return 1
}

run_auto_update() {
  # Fetch only: deployment uses an archived commit, never merges or rewrites
  # the source checkout. A failed fetch/build therefore cannot alter current.
  require_linux
  require_repo
  validate_deploy_settings
  run_pi_preflight || return 1
  if ! acquire_deployment_lock; then
    warn "Another deployment or rollback owns the deployment lock; update deferred."
    return 0
  fi
  ensure_activity_lock || return 1
  if ! recover_pending_activation; then
    write_deployment_status "failed" "recovery" "" "$(current_release_commit)" "" "" "false" "-1" "A prior activation could not be recovered; maintenance remains enabled."
    return 1
  fi
  local held_commit=""
  if sudo test -e "${MANUAL_UPDATE_HOLD_FILE}" \
    || sudo test -L "${MANUAL_UPDATE_HOLD_FILE}"; then
    if ! held_commit="$(read_manual_update_hold)"; then
      write_deployment_status "failed" "manual_update_hold" "" "$(current_release_commit)" "" "" "true" "-1" "Manual update hold metadata is unsafe; automatic deployment is blocked."
      return 1
    fi
    write_deployment_status "deferred" "manual_update_hold" "${held_commit}" "$(current_release_commit)" "" "" "true" "0" "Automatic deployment is pinned off after a manual rollback; rerun the interactive installer to re-enable it."
    return 0
  fi
  if [[ "${AUTO_UPDATE:-0}" != "1" ]]; then
    write_deployment_status "deferred" "auto_update_disabled" "" "$(current_release_commit)" "" "" "true" "0" "Automatic deployment is disabled in the root-owned deployment configuration."
    return 0
  fi
  ensure_release_layout
  if [[ "${INTEGRITY_REPAIR_ACTIVE}" -ne 1 ]]; then
    clear_stale_deployment_maintenance || return 1
  fi
  validate_runtime_secret_installation || return 1
  if ! runtime_env_has_value "OPENAI_API_KEY" || ! runtime_env_has_value "APP_ACCESS_TOKEN"; then
    write_deployment_status "failed" "configuration" "" "$(current_release_commit)" "" "" "false" "0" "Persistent device credentials are incomplete; run --configure-secrets interactively."
    return 1
  fi
  local repo_owner repo_owner_uid remote_sha failed_sha remote_advertisement
  repo_owner="$(stat -c '%U' "${REPO_DIR}/.git" 2>/dev/null || echo root)"
  repo_owner_uid="$(id -u "${repo_owner}" 2>/dev/null || printf '0')"
  if [[ "${repo_owner}" == "root" ]] || (( repo_owner_uid < 1000 )); then
    write_deployment_status "failed" "trust" "" "$(current_release_commit)" "" "" "false" "0" "Automatic updates require a normal non-root owner for the source checkout."
    return 1
  fi
  if [[ -z "${UPDATE_REMOTE_URL:-}" ]] || ! is_valid_update_remote_url "${UPDATE_REMOTE_URL}"; then
    write_deployment_status "failed" "origin" "" "$(current_release_commit)" "" "" "false" "0" "Automatic update remote is not pinned in root-owned deploy.conf."
    return 1
  fi
  git_as_owner() {
    sudo -H -u "${repo_owner}" env \
      GIT_CONFIG_NOSYSTEM=1 \
      GIT_CONFIG_GLOBAL=/dev/null \
      GIT_TERMINAL_PROMPT=0 \
      GIT_NO_REPLACE_OBJECTS=1 \
      /usr/bin/git -c core.hooksPath=/dev/null -c protocol.file.allow=never \
      -c credential.helper= -C "${REPO_DIR}" "$@"
  }
  git_transport_as_owner() {
    sudo -H -u "${repo_owner}" env \
      -u CURL_CA_BUNDLE \
      -u GIT_PROXY_COMMAND \
      -u GIT_SSL_CAINFO \
      -u GIT_SSH_COMMAND \
      -u SSL_CERT_FILE \
      ALL_PROXY= \
      HTTPS_PROXY= \
      HTTP_PROXY= \
      all_proxy= \
      https_proxy= \
      http_proxy= \
      NO_PROXY=github.com \
      no_proxy=github.com \
      GIT_CONFIG_NOSYSTEM=1 \
      GIT_CONFIG_GLOBAL=/dev/null \
      GIT_TERMINAL_PROMPT=0 \
      GIT_NO_REPLACE_OBJECTS=1 \
      GIT_SSL_NO_VERIFY=0 \
      /usr/bin/git -C / \
      -c core.hooksPath=/dev/null \
      -c protocol.file.allow=never \
      -c credential.helper= \
      -c http.sslVerify=true \
      -c http.proxy= \
      -c "http.${UPDATE_REMOTE_URL}.sslVerify=true" \
      -c "http.${UPDATE_REMOTE_URL}.proxy=" \
      "$@"
  }
  checkout_transport_config_is_safe() {
    local status
    if git_as_owner config --local --get-regexp \
      '^(include($|\.)|url\.|http\.|credential\.|protocol\.|core\.(gitproxy|sshcommand)|remote\..*\.(proxy|vcs|uploadpack|receivepack))' \
      >/dev/null 2>&1; then
      return 1
    else
      status=$?
    fi
    [[ "${status}" -eq 1 ]]
  }
  if ! checkout_transport_config_is_safe; then
    write_deployment_status "failed" "trust" "" "$(current_release_commit)" "" "" "false" "0" "Checkout-local transport, include, credential, proxy, or URL rewrite configuration is not allowed for automatic updates."
    return 1
  fi
  if ! remote_advertisement="$(
    git_transport_as_owner \
      ls-remote --exit-code --refs "${UPDATE_REMOTE_URL}" refs/heads/main
  )"; then
    write_deployment_status "failed" "resolve" "" "$(current_release_commit)" "" "" "false" "0" "Could not securely resolve the pinned GitHub main branch."
    return 1
  fi
  remote_sha="$(
    REMOTE_ADVERTISEMENT="${remote_advertisement}" python3 - <<'PY'
import os
import re

lines = os.environ["REMOTE_ADVERTISEMENT"].splitlines()
if len(lines) != 1:
    raise SystemExit(1)
fields = lines[0].split()
if (
    len(fields) != 2
    or re.fullmatch(r"[0-9a-f]{40}", fields[0]) is None
    or fields[1] != "refs/heads/main"
):
    raise SystemExit(1)
print(fields[0])
PY
  )" || {
    write_deployment_status "failed" "resolve" "" "$(current_release_commit)" "" "" "false" "0" "Pinned GitHub main returned an invalid commit advertisement."
    return 1
  }
  if ! git_transport_as_owner -C "${REPO_DIR}" fetch \
    --quiet --no-tags --no-write-fetch-head "${UPDATE_REMOTE_URL}" "${remote_sha}"; then
    write_deployment_status "failed" "fetch" "" "$(current_release_commit)" "" "" "false" "0" "git fetch failed; the active release remains unchanged."
    return 1
  fi
  if ! checkout_transport_config_is_safe \
    || ! git_as_owner cat-file -e "${remote_sha}^{commit}"; then
    write_deployment_status "failed" "trust" "" "$(current_release_commit)" "" "" "false" "0" "Fetched commit verification failed or checkout transport configuration changed."
    return 1
  fi
  failed_sha="$(last_failed_commit)"
  if [[ "${failed_sha}" == "${remote_sha}" ]] \
    && [[ "$(current_release_commit)" != "${remote_sha}" ]] \
    && [[ "${INTEGRITY_REPAIR_ACTIVE}" -ne 1 ]]; then
    write_deployment_status "deferred" "quarantined" "${remote_sha}" "$(current_release_commit)" "" "" "true" "0" "Commit previously failed; waiting for pinned main branch to change."
    return 0
  fi
  deploy_commit "${remote_sha}" "${repo_owner}" true || return 1
  if [[ "$(current_release_commit)" == "${remote_sha}" ]] \
    && release_is_complete "$(current_release_path)" "${remote_sha}" \
    && release_health_is_valid "${remote_sha}"; then
    install_backup_restore_helper
  fi
}

install_auto_update_units() {
  banner "Automatic Updates"

  local service_file="/etc/systemd/system/${SERVICE_NAME}-update.service"
  local timer_file="/etc/systemd/system/${SERVICE_NAME}-update.timer"
  local service_source timer_source
  local repo_owner repo_owner_home repo_owner_uid

  repo_owner="$(stat -c '%U' "${REPO_DIR}/.git")"
  repo_owner_uid="$(id -u "${repo_owner}" 2>/dev/null || printf '0')"
  if [[ "${repo_owner}" == "root" ]] || (( repo_owner_uid < 1000 )); then
    err "Automatic updates require the checkout to be owned by a normal non-root account."
    return 1
  fi
  if [[ -z "${UPDATE_REMOTE_URL:-}" ]] || ! is_valid_update_remote_url "${UPDATE_REMOTE_URL}"; then
    err "Automatic updates require a pinned GitHub remote in root-owned deploy.conf."
    return 1
  fi
  repo_owner_home="$(getent passwd "${repo_owner}" | cut -d: -f6)"
  if [[ -z "${repo_owner_home}" ]]; then
    err "Could not determine the home directory for repository owner ${repo_owner}."
    return 1
  fi

  local active_release active_sha helper_source backup_helper_source script_source
  active_release="$(current_release_path)"
  active_sha="$(current_release_commit)"
  if ! [[ "${active_sha}" =~ ^[0-9a-f]{40}$ ]] \
    || ! release_is_complete "${active_release}" "${active_sha}"; then
    err "Automatic updates require a verified active release."
    return 1
  fi
  script_source="${active_release}/deploy/deploy.sh"
  helper_source="${active_release}/deploy/runtime_env.py"
  backup_helper_source="${active_release}/deploy/backup_restore.py"
  if [[ ! -f "${script_source}" || -L "${script_source}" ]] \
    || [[ ! -f "${helper_source}" || -L "${helper_source}" ]] \
    || [[ ! -f "${backup_helper_source}" || -L "${backup_helper_source}" ]]; then
    err "Verified release does not contain safe deployment helper files."
    return 1
  fi

  # The privileged timer executes only immutable root-owned copies from the
  # verified release, never a raceable professor-writable checkout path.
  disable_systemd_unit_if_present "${SERVICE_NAME}-update.timer" || return 1
  stop_systemd_unit_if_present "${SERVICE_NAME}-update.service" || return 1
  sudo install -d -o root -g root -m 755 "${UPDATER_INSTALL_DIR}"
  atomic_install_root_file "${script_source}" "${UPDATER_SCRIPT}" 0755 || return 1
  atomic_install_root_file \
    "${helper_source}" "${UPDATER_INSTALL_DIR}/runtime_env.py" 0755 || return 1
  atomic_install_root_file \
    "${backup_helper_source}" "${UPDATER_INSTALL_DIR}/backup_restore.py" 0755 || return 1

  service_source="$(mktemp)"
  timer_source="$(mktemp)"
  chmod 600 "${service_source}" "${timer_source}" || {
    rm -f "${service_source}" "${timer_source}"
    return 1
  }
  if ! tee "${service_source}" >/dev/null <<EOF
[Unit]
Description=Auto-update ${SERVICE_NAME} from the pinned GitHub main branch
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment="HOME=${repo_owner_home}"
TimeoutStartSec=30min
ExecStart=${UPDATER_SCRIPT} --update
ExecStopPost=${UPDATER_SCRIPT} --recover-maintenance
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=read-only
ProtectSystem=strict
ReadWritePaths=${REPO_DIR} ${DEPLOY_DIR} ${PERSIST_DIR} ${DEPLOY_STATUS_DIR} /run
EOF
  then
    rm -f "${service_source}" "${timer_source}"
    err "Could not stage the automatic-update service unit."
    return 1
  fi

  if ! tee "${timer_source}" >/dev/null <<EOF
[Unit]
Description=Check pinned main branch for ${SERVICE_NAME} about every 30 minutes

[Timer]
OnBootSec=5min
OnUnitInactiveSec=30min
RandomizedDelaySec=5min
AccuracySec=1min
Persistent=true

[Install]
WantedBy=timers.target
EOF
  then
    rm -f "${service_source}" "${timer_source}"
    err "Could not stage the automatic-update timer unit."
    return 1
  fi

  if ! atomic_install_root_file "${service_source}" "${service_file}" 0644 \
    || ! atomic_install_root_file "${timer_source}" "${timer_file}" 0644; then
    rm -f "${service_source}" "${timer_source}"
    err "Could not safely install the automatic-update systemd units."
    return 1
  fi
  rm -f "${service_source}" "${timer_source}"
  if ! sudo systemctl daemon-reload; then
    err "Could not reload systemd after installing the automatic-update units."
    return 1
  fi
  verify_systemd_unit_install \
    "${SERVICE_NAME}-update.service" "${service_file}" "static" || return 1
  verify_systemd_unit_state \
    "${SERVICE_NAME}-update.service" "loaded" "inactive" "static" || return 1
  if ! sudo systemctl enable --now "${SERVICE_NAME}-update.timer" \
    || ! verify_systemd_unit_install \
      "${SERVICE_NAME}-update.timer" "${timer_file}" "enabled" \
    || ! verify_systemd_unit_state \
      "${SERVICE_NAME}-update.timer" "loaded" "active" "enabled"; then
    if disable_systemd_unit_if_present "${SERVICE_NAME}-update.timer"; then
      err "Automatic-update timer activation could not be verified; it was left disabled."
    else
      MAINTENANCE_RECOVERY_FAILED=1
      err "Automatic-update timer activation failed and it could not be proven disabled."
    fi
    return 1
  fi
  info "Auto-update enabled: checks the pinned GitHub main branch about every 30 minutes with randomized delay."
  info "Disable later with: sudo systemctl disable --now ${SERVICE_NAME}-update.timer"
}

install_operations_probe_units() {
  banner "Operations Probe"

  local service_file="/etc/systemd/system/${SERVICE_NAME}-operations.service"
  local timer_file="/etc/systemd/system/${SERVICE_NAME}-operations.timer"
  local service_source timer_source

  service_source="$(mktemp)"
  timer_source="$(mktemp)"
  chmod 600 "${service_source}" "${timer_source}" || {
    rm -f "${service_source}" "${timer_source}"
    return 1
  }
  if ! tee "${service_source}" >/dev/null <<EOF
[Unit]
Description=Collect sanitized operations status for ${SERVICE_NAME}
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=OPERATIONS_PROBE_PATH=${OPERATIONS_PROBE_FILE}
Environment=SERVICE_NAME=${SERVICE_NAME}
Environment=CLOUDFLARED_SERVICE_NAME=${CLOUDFLARED_SERVICE_NAME}
Environment=PERSIST_DIR=${PERSIST_DIR}
Environment=PUBLIC_HOSTNAME=${PUBLIC_HOSTNAME:-}
ExecStart=/usr/bin/python3 ${REPO_DIR}/deploy/operations_probe.py
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=read-only
ProtectSystem=strict
ReadWritePaths=${PERSIST_RUNTIME_DIR}
EOF
  then
    rm -f "${service_source}" "${timer_source}"
    err "Could not stage the operations-probe service unit."
    return 1
  fi

  if ! tee "${timer_source}" >/dev/null <<EOF
[Unit]
Description=Collect ${SERVICE_NAME} operations status every minute

[Timer]
OnBootSec=30
OnUnitInactiveSec=60
AccuracySec=5s
Persistent=true

[Install]
WantedBy=timers.target
EOF
  then
    rm -f "${service_source}" "${timer_source}"
    err "Could not stage the operations-probe timer unit."
    return 1
  fi

  if ! atomic_install_root_file "${service_source}" "${service_file}" 0644 \
    || ! atomic_install_root_file "${timer_source}" "${timer_file}" 0644; then
    rm -f "${service_source}" "${timer_source}"
    err "Could not safely install the operations-probe systemd units."
    return 1
  fi
  rm -f "${service_source}" "${timer_source}"
  if ! sudo systemctl daemon-reload; then
    err "Could not reload systemd after installing the operations-probe units."
    return 1
  fi
  verify_systemd_unit_install \
    "${SERVICE_NAME}-operations.service" "${service_file}" "static" || return 1
  verify_systemd_unit_state \
    "${SERVICE_NAME}-operations.service" "loaded" "inactive" "static" || return 1
  if ! sudo systemctl enable --now "${SERVICE_NAME}-operations.timer" \
    || ! verify_systemd_unit_install \
      "${SERVICE_NAME}-operations.timer" "${timer_file}" "enabled" \
    || ! verify_systemd_unit_state \
      "${SERVICE_NAME}-operations.timer" "loaded" "active" "enabled"; then
    if disable_systemd_unit_if_present "${SERVICE_NAME}-operations.timer"; then
      err "Operations-probe timer activation could not be verified; it was left disabled."
    else
      MAINTENANCE_RECOVERY_FAILED=1
      err "Operations-probe timer activation failed and it could not be proven disabled."
    fi
    return 1
  fi
  sudo systemctl start "${SERVICE_NAME}-operations.service" || true
  sudo chown "${APP_USER}:${APP_USER}" "${OPERATIONS_PROBE_FILE}" 2>/dev/null || true
  info "Operations probe enabled: sudo systemctl status ${SERVICE_NAME}-operations.timer"
}

restart_backend_after_secret_change() {
  local load_state
  if ! load_state="$(systemd_unit_property "${SERVICE_NAME}.service" "LoadState")"; then
    MAINTENANCE_RECOVERY_FAILED=1
    err "Could not determine whether the backend service exists; maintenance remains enabled."
    return 1
  fi
  if [[ "${load_state}" == "not-found" ]]; then
    leave_deployment_maintenance
    return 0
  fi
  if [[ -z "${DEPLOY_LOCK_FD}" ]]; then
    err "Runtime-setting restart requires the deployment lock already held by its preparation phase."
    return 1
  fi
  if sudo systemctl restart "${SERVICE_NAME}" && wait_for_backend_health; then
    leave_deployment_maintenance
    return 0
  fi
  MAINTENANCE_RECOVERY_FAILED=1
  sudo systemctl stop "${SERVICE_NAME}" >/dev/null 2>&1 || true
  err "The backend did not recover after the setting change; it is stopped and maintenance remains enabled."
  return 1
}

prepare_secret_management() {
  require_linux
  if [[ "${EUID}" -eq 0 ]]; then
    err "Run the interactive installer as the normal sudo-capable professor account, not with 'sudo bash'."
    err "The script requests sudo only for scoped device changes and keeps Cloudflare login state under the invoking account."
    exit 1
  fi
  require_repo
  validate_deploy_settings
  require_sudo_access
  if ! acquire_deployment_lock; then
    err "Another deployment or rollback is in progress."
    return 1
  fi
  install_runtime_env_helper
  ensure_runtime_environment_file
  recover_pending_activation || return 1
  if [[ "${INTEGRITY_REPAIR_ACTIVE}" -eq 1 ]]; then
    err "Runtime settings cannot change until the interrupted integrity repair completes."
    return 1
  fi
  clear_stale_deployment_maintenance || return 1
  if systemctl cat "${SERVICE_NAME}" >/dev/null 2>&1; then
    enter_deployment_maintenance "runtime_config" || return 1
    sleep 1
    if (( $(active_workflow_count) != 0 )); then
      leave_deployment_maintenance
      err "Runtime setting changes require zero active workflows; try again after the current run finishes."
      return 1
    fi
  elif [[ -n "$(backend_port_pids)" ]]; then
    err "No managed backend is installed, but port ${BACKEND_PORT} has a listener; configuration change refused."
    return 1
  fi
}

configure_donation_command() {
  local donation_url=""
  prepare_secret_management
  if [[ ! -t 0 ]]; then
    err "Donation configuration is interactive; rerun from a terminal."
    return 1
  fi
  read -rp "Hosted HTTPS PayPal donation URL (blank disables donations): " donation_url
  donation_url="$(trim_value "${donation_url}")"
  if [[ -z "${donation_url}" ]]; then
    clear_runtime_setting "PAYPAL_DONATE_URL"
    info "PayPal donation link disabled."
  else
    if ! printf '%s' "${donation_url}" \
      | sudo "${RUNTIME_ENV_HELPER}" set "${RUNTIME_ENV_FILE}" "PAYPAL_DONATE_URL"; then
      err "Donation URL was not changed. Use an https://paypal.com, https://www.paypal.com, or https://paypal.me hosted URL."
      return 1
    fi
    info "PayPal donation link configured without storing payment credentials."
  fi
  restart_backend_after_secret_change
}

configure_runtime_policy_command() {
  local key current value index policy_payload
  local keys=(
    APP_RATE_LIMIT_REQUESTS
    APP_RATE_LIMIT_WINDOW_SECONDS
    UPLOAD_RETENTION_SECONDS
    OUTPUT_RETENTION_SECONDS
    WORKFLOW_TTL_SECONDS
    WORKFLOW_MAX_ENTRIES
    OPENAI_WORKFLOW_CONCURRENCY
    BATCH_WORKFLOW_CONCURRENCY
    MAX_GENERATED_QUESTIONS
  )
  local defaults=(12 60 86400 604800 86400 200 1 2 100)
  local values=()
  prepare_secret_management
  if [[ ! -t 0 ]]; then
    err "Runtime-policy configuration is interactive; rerun from a terminal."
    return 1
  fi
  for ((index = 0; index < ${#keys[@]}; index += 1)); do
    key="${keys[index]}"
    current="$(
      sudo "${RUNTIME_ENV_HELPER}" get "${RUNTIME_ENV_FILE}" "${key}" 2>/dev/null \
        || printf '%s' "${defaults[index]}"
    )"
    value="$(prompt_with_default "${key}" "${current}")"
    values[index]="${value}"
  done
  policy_payload="$(
    python3 - "${#keys[@]}" "${keys[@]}" "${values[@]}" <<'PY'
import json
import sys

count = int(sys.argv[1])
keys = sys.argv[2 : 2 + count]
values = sys.argv[2 + count :]
if len(values) != count:
    raise SystemExit(1)
print(json.dumps(dict(zip(keys, values, strict=True)), separators=(",", ":")))
PY
  )"
  if ! printf '%s' "${policy_payload}" \
    | sudo "${RUNTIME_ENV_HELPER}" set-many "${RUNTIME_ENV_FILE}"; then
    err "Runtime policy was not applied; correct the reported value and rerun."
    return 1
  fi
  restart_backend_after_secret_change
  info "Rate, retention, concurrency, and generation limits are configured."
}

configure_secrets_command() {
  prepare_secret_management
  prompt_for_openai_key false
  generate_access_token false
  show_one_time_access_token
  restart_backend_after_secret_change
  info "Persistent device credentials are configured."
}

rotate_openai_key_command() {
  prepare_secret_management
  prompt_for_openai_key true
  restart_backend_after_secret_change
  info "OpenAI API key rotation complete."
}

rotate_access_token_command() {
  prepare_secret_management
  generate_access_token true
  show_one_time_access_token
  restart_backend_after_secret_change
  info "Browser access token rotation complete. Existing browser sessions must authenticate again."
}

print_deploy_status() {
  require_linux
  require_sudo_access

  local service_state timer_state current key_state="missing" token_state="missing"
  service_state="$(systemctl is-active "${SERVICE_NAME}" 2>/dev/null || true)"
  timer_state="$(systemctl is-enabled "${SERVICE_NAME}-update.timer" 2>/dev/null || true)"
  current="$(current_release_path)"
  if [[ -x "${RUNTIME_ENV_HELPER}" ]] && sudo test -f "${RUNTIME_ENV_FILE}"; then
    sudo "${RUNTIME_ENV_HELPER}" has "${RUNTIME_ENV_FILE}" "OPENAI_API_KEY" && key_state="configured"
    sudo "${RUNTIME_ENV_HELPER}" has "${RUNTIME_ENV_FILE}" "APP_ACCESS_TOKEN" && token_state="configured"
  fi

  printf 'Backend service: %s\n' "${service_state:-unknown}"
  printf 'Automatic updates: %s\n' "${timer_state:-disabled}"
  printf 'Active release: %s\n' "${current:-none}"
  printf 'OpenAI API key: %s\n' "${key_state}"
  printf 'Browser access token: %s\n' "${token_state}"
  if sudo test -e "${RUNTIME_ENV_FILE}"; then
    sudo stat -c 'Device environment: %n (%a %U:%G)' "${RUNTIME_ENV_FILE}"
  else
    printf 'Device environment: missing (%s)\n' "${RUNTIME_ENV_FILE}"
  fi
  if "${CURL_BIN}" -q --noproxy '*' --proto '=http' \
    --connect-timeout 2 --max-time 5 -fsS \
    "http://127.0.0.1:${BACKEND_PORT}/api/health" >/dev/null; then
    printf 'Local health: reachable\n'
  else
    printf 'Local health: unavailable\n'
  fi
}

recover_maintenance_command() {
  require_linux
  validate_deploy_settings
  require_sudo_access
  if ! acquire_deployment_lock; then
    err "A deployment or rollback is still active; maintenance mode was not cleared."
    return 1
  fi
  if sudo test -e "${ACTIVATION_STATE_FILE}" || sudo test -L "${ACTIVATION_STATE_FILE}"; then
    recover_pending_activation || return 1
  fi
  if sudo test -e "${ACTIVATION_STATE_FILE}" || sudo test -L "${ACTIVATION_STATE_FILE}" \
    || sudo test -e "${INTEGRITY_STATE_FILE}" || sudo test -L "${INTEGRITY_STATE_FILE}" \
    || sudo test -e "${RESTORE_STATE_FILE}" || sudo test -L "${RESTORE_STATE_FILE}"; then
    err "A deployment or restore transaction is still pending; maintenance was not cleared."
    return 1
  fi
  local current current_sha active_state
  current="$(current_release_path)"
  current_sha="$(current_release_commit)"
  if [[ -z "${current}" ]] || ! selected_release_is_verified; then
    stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
    err "No verified direct release is selected; maintenance mode was not cleared."
    return 1
  fi
  if sudo test -e "${MAINTENANCE_FILE}" || sudo test -L "${MAINTENANCE_FILE}"; then
    read_deployment_maintenance_reason >/dev/null || {
      err "Maintenance metadata is unsafe; it was not removed."
      return 1
    }
    acquire_activity_lock_exclusive || return 1
  fi
  verify_systemd_unit_install \
    "${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service" "enabled" \
    || return 1
  active_state="$(systemd_unit_property "${SERVICE_NAME}.service" "ActiveState")" || return 1
  if [[ "${active_state}" != "active" ]]; then
    restart_installed_backend_service || return 1
  else
    verify_systemd_unit_state \
      "${SERVICE_NAME}.service" "loaded" "active" "enabled" || return 1
  fi
  release_health_is_valid "${current_sha}" || return 1
  if sudo test -e "${MAINTENANCE_FILE}" || sudo test -L "${MAINTENANCE_FILE}"; then
    atomic_remove_root_path "${MAINTENANCE_FILE}" "/run" "regular" || return 1
  fi
  MAINTENANCE_ACTIVE=0
  release_activity_lock
  info "Verified the selected release and cleared stale deployment maintenance."
}

pin_manual_rollback_updates() {
  local target_sha="$1"

  if ! disable_systemd_unit_if_present "${SERVICE_NAME}-update.timer"; then
    err "Could not prove the automatic-update timer is disabled."
    return 1
  fi
  AUTO_UPDATE=0
  save_deploy_config || return 1
  persist_manual_update_hold "${target_sha}" || {
    err "Could not persist the manual rollback update hold."
    return 1
  }
}

manual_rollback() {
  local target_sha="$1"
  if ! [[ "${target_sha}" =~ ^[0-9a-f]{40}$ ]]; then
    err "Rollback target must be a full 40-character lowercase commit SHA."
    return 1
  fi

  require_linux
  require_repo
  validate_deploy_settings
  require_sudo_access
  if ! acquire_deployment_lock; then
    err "Another deployment or rollback is already in progress."
    return 1
  fi
  ensure_release_layout
  recover_pending_activation || return 1

  local target="${RELEASES_DIR}/${target_sha}"
  local previous previous_sha workflow_count
  if ! release_is_verified_direct "${target}" "${target_sha}"; then
    err "Rollback target is missing or does not have a valid .release-ready marker: ${target}"
    return 1
  fi
  previous="$(current_release_path)"
  previous_sha="$(current_release_commit)"
  if [[ -e "${CURRENT_RELEASE}" || -L "${CURRENT_RELEASE}" ]] \
    && ! selected_release_is_verified; then
    err "Current release is not a verified direct release; rollback was not attempted."
    return 1
  fi
  if [[ "${previous}" == "${target}" && "${INTEGRITY_REPAIR_ACTIVE}" -ne 1 ]]; then
    pin_manual_rollback_updates "${target_sha}" || return 1
    if ! verify_systemd_unit_install \
      "${SERVICE_NAME}.service" "/etc/systemd/system/${SERVICE_NAME}.service" "enabled" \
      || ! verify_systemd_unit_state \
        "${SERVICE_NAME}.service" "loaded" "active" "enabled" \
      || ! release_health_is_valid "${target_sha}"; then
      write_deployment_status "failed" "manual_rollback_unhealthy" "${target_sha}" "${target_sha}" "${previous_sha}" "" "false" "0" "Requested rollback release was already selected, but its exact service, unit, or commit health check failed; automatic updates remain pinned off."
      err "Release ${target_sha} is selected but is not serving through the exact healthy backend unit."
      return 1
    fi
    write_deployment_status "success" "manual_rollback" "${target_sha}" "${target_sha}" "${previous_sha}" "" "false" "0" "Requested rollback release was already active; automatic updates remain pinned off."
    info "Release ${target_sha} is already active and passed exact service health checks."
    return 0
  fi
  enter_deployment_maintenance "manual_rollback" || return 1
  sleep 1
  workflow_count="$(active_workflow_count)"
  if (( workflow_count != 0 )); then
    leave_deployment_maintenance
    err "Rollback requires a confirmed zero active-workflow count; received ${workflow_count}."
    return 1
  fi
  if ! release_is_verified_direct "${target}" "${target_sha}" \
    || { [[ -n "${previous}" ]] \
      && ! release_is_verified_direct "${previous}" "${previous_sha}"; }; then
    MAINTENANCE_RECOVERY_FAILED=1
    stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
    err "A rollback release changed after validation; maintenance remains enabled."
    return 1
  fi
  pin_manual_rollback_updates "${target_sha}" || return 1

  write_deployment_status "activating" "manual_rollback" "${target_sha}" "${previous_sha}" "${previous_sha}" "" "false" "0" "Activating a manually selected prior release."
  PENDING_ACTIVATION=1
  if ! persist_activation_transaction \
    "${previous}" "${previous_sha}" "${target}" "${target_sha}" "false"; then
    PENDING_ACTIVATION=0
    err "Could not persist rollback recovery metadata; current release was not changed."
    return 1
  fi
  switch_current_release "${target}" || return 1
  sudo install -o root -g root -m 600 /dev/null "${ACTIVATION_RECOVERY_ALLOW_FILE}"
  if install_backend_service && release_health_is_valid "${target_sha}"; then
    sudo rm -f "${ACTIVATION_RECOVERY_ALLOW_FILE}"
    clear_activation_transaction
    PENDING_ACTIVATION=0
    if [[ "${INTEGRITY_REPAIR_ACTIVE}" -eq 1 ]]; then
      clear_integrity_repair_state || return 1
      INTEGRITY_REPAIR_ACTIVE=0
    fi
    leave_deployment_maintenance
    write_deployment_status "success" "manual_rollback" "${target_sha}" "${target_sha}" "${previous_sha}" "" "false" "0" "Manual rollback completed and passed health checks."
    info "Manual rollback completed: ${target_sha}"
    return 0
  fi

  err "Selected release failed health checks; restoring the prior release."
  if [[ -n "${previous}" ]] \
    && release_is_verified_direct "${previous}" "${previous_sha}"; then
    switch_current_release "${previous}" || return 1
    if install_backend_service && release_health_is_valid "${previous_sha}"; then
      sudo rm -f "${ACTIVATION_RECOVERY_ALLOW_FILE}"
      clear_activation_transaction
      PENDING_ACTIVATION=0
      leave_deployment_maintenance
      write_deployment_status "rolled_back" "manual_rollback_failed" "${target_sha}" "${previous_sha}" "${previous_sha}" "true" "false" "0" "Selected manual rollback target failed; the prior release was restored and verified healthy."
      return 1
    fi
  fi
  sudo rm -f "${ACTIVATION_RECOVERY_ALLOW_FILE}"
  MAINTENANCE_RECOVERY_FAILED=1
  stop_systemd_unit_if_present "${SERVICE_NAME}.service" || true
  write_deployment_status "failed" "manual_rollback_failed" "${target_sha}" "$(current_release_commit)" "${previous_sha}" "false" "false" "0" "Selected rollback target failed and the prior release could not be verified healthy."
  return 1
}

validate_inherited_restore_lock() {
  local lock_fd="$1"
  local probe_fd=""

  if ! [[ "${lock_fd}" =~ ^[0-9]+$ ]] \
    || [[ ! -e "/proc/$$/fd/${lock_fd}" ]] \
    || [[ "$(readlink -f "/proc/$$/fd/${lock_fd}" 2>/dev/null || true)" \
      != "${DEPLOY_LOCK_FILE}" ]]; then
    err "The restore installer did not inherit the parent deployment lock descriptor."
    return 1
  fi
  if [[ "$(stat -c '%U:%G:%a' "${DEPLOY_LOCK_DIR}" 2>/dev/null || true)" \
    != "root:${DEPLOY_ADMIN_GROUP}:750" ]] \
    || [[ "$(stat -c '%U:%G:%a:%h' "${DEPLOY_LOCK_FILE}" 2>/dev/null || true)" \
      != "root:${DEPLOY_ADMIN_GROUP}:440:1" ]]; then
    err "Deployment lock metadata is unsafe."
    return 1
  fi
  if ! exec {probe_fd}<"${DEPLOY_LOCK_FILE}"; then
    err "Could not open a deployment-lock probe descriptor."
    return 1
  fi
  if flock -n "${probe_fd}"; then
    flock -u "${probe_fd}" || true
    exec {probe_fd}>&-
    err "The backup/restore parent does not own the deployment lock."
    return 1
  fi
  exec {probe_fd}>&-
  if ! flock -n "${lock_fd}"; then
    err "The inherited descriptor is not the lock-owning file description."
    return 1
  fi
  DEPLOY_LOCK_FD="${lock_fd}"
}

validate_inherited_activity_lock() {
  local lock_fd="$1"
  local probe_fd=""
  local descriptor_identity path_identity

  if ! [[ "${lock_fd}" =~ ^[0-9]+$ ]] \
    || [[ ! -e "/proc/$$/fd/${lock_fd}" ]] \
    || [[ "$(readlink -f "/proc/$$/fd/${lock_fd}" 2>/dev/null || true)" \
      != "${ACTIVITY_LOCK_FILE}" ]]; then
    err "The restore installer did not inherit the parent paid-work activity lock."
    return 1
  fi
  if [[ "$(stat -c '%U:%G:%a' "${ACTIVITY_LOCK_DIR}" 2>/dev/null || true)" \
    != "root:root:755" ]] \
    || [[ "$(stat -c '%U:%G:%a:%h' "${ACTIVITY_LOCK_FILE}" 2>/dev/null || true)" \
      != "root:root:444:1" ]]; then
    err "Paid-work activity lock metadata is unsafe."
    return 1
  fi
  descriptor_identity="$(
    stat -Lc '%d:%i' "/proc/$$/fd/${lock_fd}" 2>/dev/null || true
  )"
  path_identity="$(stat -Lc '%d:%i' "${ACTIVITY_LOCK_FILE}" 2>/dev/null || true)"
  if [[ -z "${descriptor_identity}" || "${descriptor_identity}" != "${path_identity}" ]]; then
    err "The inherited paid-work activity lock inode is no longer current."
    return 1
  fi
  if ! exec {probe_fd}<"${ACTIVITY_LOCK_FILE}"; then
    err "Could not open a paid-work activity-lock probe descriptor."
    return 1
  fi
  if flock -s -n "${probe_fd}"; then
    flock -u "${probe_fd}" || true
    exec {probe_fd}>&-
    err "The backup/restore parent does not own the exclusive activity lock."
    return 1
  fi
  exec {probe_fd}>&-
  if ! flock -x -n "${lock_fd}"; then
    err "The inherited activity descriptor is not the lock-owning file description."
    return 1
  fi
  ACTIVITY_LOCK_FD="${lock_fd}"
  ACTIVITY_LOCK_INHERITED=1
}

validate_restore_install_context() {
  local expected_commit="$1"
  local status_payload maintenance_reason lock_fd activity_lock_fd
  local installer_root="${UCONN_RESTORE_INSTALLER_ROOT:-}"

  if [[ "${UCONN_RESTORE_ORCHESTRATED:-}" != "1" ]] \
    || [[ "${RESTORE_INSTALL_INNER}" -ne 1 ]]; then
    err "The restore-install inner mode is reserved for the installed backup/restore helper."
    return 1
  fi
  if [[ "${EUID}" -ne 0 ]]; then
    err "The restore-install inner mode must remain a root child of the backup/restore helper."
    return 1
  fi
  if ! [[ "${expected_commit}" =~ ^[0-9a-f]{40}$ ]]; then
    err "Restore install requires one full lowercase 40-character commit SHA."
    return 1
  fi
  if [[ -z "${installer_root}" ]] \
    || [[ "${DEFAULT_REPO_DIR}" != "${installer_root}" ]] \
    || [[ "${SCRIPT_DIR}" != "${installer_root}/deploy" ]]; then
    err "Restore installer is not executing from the journaled root-owned snapshot."
    return 1
  fi
  if [[ ! -f "${BACKUP_RESTORE_HELPER}" || -L "${BACKUP_RESTORE_HELPER}" ]] \
    || [[ "$(stat -c '%U:%G:%a' "${BACKUP_RESTORE_HELPER}" 2>/dev/null || true)" \
      != "root:root:755" ]]; then
    err "The installed backup/restore helper is missing or unsafe."
    return 1
  fi
  if [[ ! -f "${RESTORE_STATE_FILE}" || -L "${RESTORE_STATE_FILE}" ]] \
    || [[ "$(stat -c '%U:%G:%a' "${RESTORE_STATE_FILE}" 2>/dev/null || true)" \
      != "root:root:600" ]]; then
    err "The durable restore journal is missing or unsafe."
    return 1
  fi
  if [[ ! -f "${RESTORE_INSTALL_ALLOW_FILE}" || -L "${RESTORE_INSTALL_ALLOW_FILE}" ]] \
    || [[ "$(stat -c '%U:%G:%a' "${RESTORE_INSTALL_ALLOW_FILE}" 2>/dev/null || true)" \
      != "root:root:600" ]]; then
    err "The ephemeral restore-install authorization is missing or unsafe."
    return 1
  fi
  if [[ ! -f "${MAINTENANCE_FILE}" || -L "${MAINTENANCE_FILE}" ]] \
    || [[ "$(stat -c '%U:%G:%a' "${MAINTENANCE_FILE}" 2>/dev/null || true)" \
      != "root:root:644" ]]; then
    err "Deployment maintenance is not safely active for restore installation."
    return 1
  fi
  maintenance_reason="$(read_deployment_maintenance_reason)" || return 1
  if [[ "${maintenance_reason}" != "deployment" ]]; then
    err "Restore installation requires the backup/restore deployment-maintenance marker."
    return 1
  fi
  if ! status_payload="$("${BACKUP_RESTORE_HELPER}" status)" \
    || ! STATUS_PAYLOAD="${status_payload}" python3 - \
      "${expected_commit}" "${CONFIGURED_SOURCE_REPO_DIR}" "${installer_root}" <<'PY'
import json
import os
import re
import sys
from pathlib import Path

expected = sys.argv[1]
checkout = sys.argv[2]
installer = sys.argv[3]
payload = json.loads(os.environ["STATUS_PAYLOAD"])
if (
    not isinstance(payload, dict)
    or set(payload)
    != {
        "commit",
        "pending",
        "phase",
        "transaction_id",
        "checkout",
        "installer_root",
        "installer_digest",
    }
    or payload["pending"] is not True
    or payload["phase"] != "installing"
    or payload["commit"] != expected
    or re.fullmatch(
        r"[0-9]{8}T[0-9]{6}Z-[0-9a-f]{16}",
        str(payload["transaction_id"]),
    )
    is None
    or payload["checkout"] != checkout
    or payload["installer_root"] != installer
    or payload["installer_root"]
    != f"/var/lib/uconn-isomorphic-restore/installer-{payload['transaction_id']}"
    or not Path(payload["checkout"]).is_absolute()
    or ".." in Path(payload["checkout"]).parts
    or re.fullmatch(r"[0-9a-f]{64}", str(payload["installer_digest"])) is None
):
    raise SystemExit(1)
PY
  then
    err "Installed backup/restore helper did not confirm this exact installing transaction."
    return 1
  fi

  lock_fd="${UCONN_RESTORE_LOCK_FD:-}"
  validate_inherited_restore_lock "${lock_fd}" || return 1
  activity_lock_fd="${UCONN_RESTORE_ACTIVITY_LOCK_FD:-}"
  validate_inherited_activity_lock "${activity_lock_fd}"
}

restore_install_inner() {
  local expected_commit="$1"
  local configured_remote checkout_remote repo_owner initial_sha
  local installer_repo

  require_linux
  validate_restore_install_context "${expected_commit}"
  installer_repo="${REPO_DIR}"
  require_repo
  validate_deploy_settings
  run_pi_preflight
  sync_etc_hosts
  validate_runtime_secret_installation
  if ! runtime_env_has_value "OPENAI_API_KEY" \
    || ! runtime_env_has_value "APP_ACCESS_TOKEN"; then
    err "Restored runtime credentials are incomplete; no service was exposed."
    return 1
  fi
  recover_pending_activation

  configured_remote="${UPDATE_REMOTE_URL:-}"
  checkout_remote="$(git -C "${REPO_DIR}" remote get-url origin 2>/dev/null || true)"
  if [[ -z "${configured_remote}" || "${checkout_remote}" != "${configured_remote}" ]] \
    || ! [[ "${configured_remote}" =~ ^https://github\.com/ ]]; then
    err "Restore checkout origin does not match the backed-up pinned HTTPS GitHub remote."
    return 1
  fi
  UPDATE_REMOTE_URL="${checkout_remote}"

  install_system_packages
  ensure_activity_lock
  configure_local_domain
  ensure_release_layout
  repo_owner="$(stat -c '%U' "${REPO_DIR}/.git" 2>/dev/null || echo root)"
  if [[ "${repo_owner}" != "root" ]] || [[ "${REPO_DIR}" != "${installer_repo}" ]]; then
    err "Restore installation source must remain the journaled root-owned snapshot."
    return 1
  fi
  initial_sha="$(sudo -H -u root env GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    GIT_NO_REPLACE_OBJECTS=1 git -c core.hooksPath=/dev/null -c credential.helper= \
    -C "${REPO_DIR}" rev-parse HEAD)"
  if [[ "${initial_sha}" != "${expected_commit}" ]]; then
    err "Restore checkout moved away from the journaled backup commit."
    return 1
  fi

  deploy_commit "${expected_commit}" "${repo_owner}" false
  if [[ "$(current_release_commit)" != "${expected_commit}" ]] \
    || ! release_is_complete "$(current_release_path)" "${expected_commit}" \
    || ! release_health_is_valid "${expected_commit}"; then
    err "The journaled backup commit was not activated and verified."
    return 1
  fi

  disable_cloudflare_public_hosting
  AUTO_UPDATE=0
  disable_systemd_unit_if_present "${SERVICE_NAME}-update.timer"
  REPO_DIR="${CONFIGURED_SOURCE_REPO_DIR}"
  save_deploy_config
  install_backend_service
  install_nginx_config
  if ! release_health_is_valid "${expected_commit}"; then
    err "The local-only restored service stopped serving the exact backup commit."
    return 1
  fi

  banner "Restore Install Staged"
  info "Exact commit ${expected_commit} is healthy behind maintenance."
  info "The backup/restore parent will remove the journal and maintenance markers."
}

main() {
  if [[ "${RESTORE_INSTALL_INNER}" -ne 1 ]] \
    && [[ "${1:-}" != "--status" && "${1:-}" != "--preflight" ]] \
    && { [[ -e "${RESTORE_STATE_FILE}" ]] || [[ -L "${RESTORE_STATE_FILE}" ]]; }; then
    err "A restore transaction is pending at ${RESTORE_STATE_FILE}."
    err "Complete the documented offline access-token rotation or recover the retained pre-restore roots before deploying."
    exit 1
  fi

  case "${1:-}" in
    --help|-h)
      print_usage
      exit 0
      ;;
    --restore-install-inner)
      if [[ "$#" -ne 2 ]]; then
        err "Invalid internal restore-install invocation."
        exit 2
      fi
      restore_install_inner "$2"
      exit $?
      ;;
    --update)
      run_auto_update
      exit $?
      ;;
    --preflight)
      require_linux
      require_repo
      validate_deploy_settings
      run_pi_preflight
      exit $?
      ;;
    --configure-secrets)
      configure_secrets_command
      exit $?
      ;;
    --rotate-openai-key)
      rotate_openai_key_command
      exit $?
      ;;
    --rotate-access-token)
      rotate_access_token_command
      exit $?
      ;;
    --configure-donation)
      configure_donation_command
      exit $?
      ;;
    --configure-runtime-policy)
      configure_runtime_policy_command
      exit $?
      ;;
    --status)
      print_deploy_status
      exit $?
      ;;
    --recover-maintenance)
      recover_maintenance_command
      exit $?
      ;;
    --rollback)
      if [[ -z "${2:-}" ]]; then
        err "Usage: bash deploy/deploy.sh --rollback <full-commit-sha>"
        exit 2
      fi
      manual_rollback "$2"
      exit $?
      ;;
    "")
      ;;
    *)
      err "Unknown option: $1"
      err "Supported: --help, --preflight, --configure-secrets, --rotate-openai-key, --rotate-access-token, --configure-donation, --configure-runtime-policy, --status, --recover-maintenance, --rollback <sha>, --update"
      exit 2
      ;;
  esac

  banner "UConn Isomorphic Question LLM Installer"
  select_platform

  echo "This script will install and create everything necessary to run this site on a Raspberry Pi."
  echo "It will install system packages, create ${DEPLOY_DIR}, build the frontend, install the backend service, configure nginx, and use the existing local address ${LOCAL_DOMAIN}."
  echo ""

  if ! ask_to_continue "Continue with Raspberry Pi setup?"; then
    info "Closed without making changes."
    exit 0
  fi

  require_linux
  if [[ "${EUID}" -eq 0 ]]; then
    err "Run the interactive installer as the normal sudo-capable professor account, not with 'sudo bash'."
    err "The script requests sudo only for scoped device changes and keeps Cloudflare login state under the invoking account."
    exit 1
  fi
  require_repo
  validate_deploy_settings
  run_pi_preflight
  require_sudo
  if ! acquire_deployment_lock; then
    err "Another deployment or rollback is already in progress."
    exit 1
  fi
  install_runtime_env_helper
  ensure_runtime_environment_file
  recover_pending_activation || exit 1
  ensure_runtime_secrets
  show_one_time_access_token
  if [[ "${INTEGRITY_REPAIR_ACTIVE}" -ne 1 ]]; then
    clear_stale_deployment_maintenance || exit 1
  fi
  UPDATE_REMOTE_URL="$(git -C "${REPO_DIR}" remote get-url origin 2>/dev/null || true)"
  if [[ -n "${UPDATE_REMOTE_URL}" ]] && ! is_valid_update_remote_url "${UPDATE_REMOTE_URL}"; then
    warn "The checkout origin is not a supported pinned GitHub URL; automatic updates will remain unavailable."
    unset UPDATE_REMOTE_URL
  fi
  save_deploy_config
  install_system_packages
  ensure_activity_lock
  configure_local_domain
  ensure_release_layout
  repo_owner="$(stat -c '%U' "${REPO_DIR}/.git" 2>/dev/null || echo root)"
  initial_sha="$(sudo -u "${repo_owner}" git -C "${REPO_DIR}" rev-parse HEAD)"
  deploy_commit "${initial_sha}" "${repo_owner}" false
  if [[ "$(current_release_commit)" != "${initial_sha}" ]] \
    || ! release_is_complete "$(current_release_path)" "${initial_sha}" \
    || ! release_health_is_valid "${initial_sha}"; then
    err "The reviewed checkout commit was not activated and verified; release-dependent setup was not changed."
    err "Wait for active workflows to finish, then rerun the installer."
    exit 1
  fi
  install_backup_restore_helper
  install_nginx_config
  warn_if_openai_key_missing
  verify_local_site

  echo ""
  warn "Public hosting exposes the login surface to the internet."
  warn "Keep this disabled unless APP_ACCESS_TOKEN is configured and Cloudflare Access is also enforced."
  if confirm_default_no "Configure optional Cloudflare public hosting?"; then
    setup_cloudflare
  else
    apply_local_only_hosting
    info "Public tunnel hosting is disabled. Local site remains available at http://${LOCAL_DOMAIN}"
  fi
  install_operations_probe_units

  echo ""
  warn "Automatic updates rebuild and restart the app from the root-pinned GitHub main branch without professor review."
  warn "Leave this disabled unless the repository and Git credentials are controlled and tested."
  if confirm_default_no "Enable automatic updates from the pinned main branch about every 30 minutes?"; then
    if ! install_auto_update_units; then
      if ! disable_systemd_unit_if_present "${SERVICE_NAME}-update.timer"; then
        MAINTENANCE_RECOVERY_FAILED=1
        err "The failed automatic-update installation could not be proven quiescent."
      fi
      err "Automatic updates were not enabled."
      exit 1
    fi
    if ! clear_manual_update_hold; then
      if disable_systemd_unit_if_present "${SERVICE_NAME}-update.timer"; then
        err "The prior manual-update hold could not be cleared; the timer was disabled."
      else
        MAINTENANCE_RECOVERY_FAILED=1
        err "The manual-update hold could not be cleared and the timer could not be proven disabled."
      fi
      exit 1
    fi
    AUTO_UPDATE=1
  else
    disable_systemd_unit_if_present "${SERVICE_NAME}-update.timer" || exit 1
    AUTO_UPDATE=0
    info "Automatic updates disabled."
  fi
  save_deploy_config

  banner "Complete"
  info "Local site: http://${LOCAL_DOMAIN}"
  info "Backend service: sudo systemctl status ${SERVICE_NAME}"
  info "Logs: sudo journalctl -u ${SERVICE_NAME} -f"
  if [[ "${AUTO_UPDATE:-0}" == "1" ]]; then
    info "Auto-update: enabled (pinned main branch about every 30 minutes) — sudo systemctl status ${SERVICE_NAME}-update.timer"
  fi
  warn_if_openai_key_missing
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
