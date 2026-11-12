# Raspberry Pi Deployment

This folder has three launchers:

- `deploy.sh` installs and configures the Raspberry Pi runtime.
- `preview.mjs` runs cross-platform local previews at `localhost` on Windows,
  macOS, and Linux.
- `preview.sh` remains available as the Unix shell preview launcher.

## Supported Raspberry Pi

The production installer supports **64-bit Raspberry Pi OS only**
(`aarch64` kernel and `arm64` packages). Use a Pi with at least 2 GB-class RAM;
4 GB or more is recommended. Before changing the device, the installer requires:

- Python 3.10 or newer.
- At least 8 GiB free on both the release and persistent-data filesystems.
- The repository's committed frontend lockfile.

The production path is not supported on 32-bit `armhf`/`armv7l`. Mac and Windows
use the preview commands later in this document.

## First Install

Log in as the normal professor/deployment user, not `root`. On a fresh Pi,
install the clone prerequisites, clone the reviewed GitHub repository into
that user's home, and prove the configured remote can be read without an
interactive credential prompt:

```bash
(
set -euo pipefail
sudo apt-get update
sudo apt-get install -y ca-certificates git
read -r -p 'Reviewed 40-hex release commit: ' RELEASE_COMMIT
printf '%s\n' "${RELEASE_COMMIT}" | grep -Eq '^[0-9a-f]{40}$'
git clone https://github.com/lindimismaili/UConn-IsomorphicQuestionLLM.git \
  UConn-IsomorphicQuestionLLM
cd UConn-IsomorphicQuestionLLM
test "$(stat -c %U .)" = "$USER"
git remote -v
GIT_TERMINAL_PROMPT=0 git ls-remote --exit-code origin refs/heads/main
GIT_TERMINAL_PROMPT=0 GIT_NO_REPLACE_OBJECTS=1 \
  git -c core.hooksPath=/dev/null -c protocol.file.allow=never \
  fetch origin "${RELEASE_COMMIT}"
GIT_NO_REPLACE_OBJECTS=1 \
  git -c core.hooksPath=/dev/null checkout --detach "${RELEASE_COMMIT}"
test "$(GIT_NO_REPLACE_OBJECTS=1 git rev-parse HEAD)" = "${RELEASE_COMMIT}"
bash deploy/deploy.sh --preflight
bash deploy/deploy.sh
)
```

For a private repository, configure a read-only repository deploy key or
equivalent least-privilege Git credential for this normal user before the
`ls-remote` check. Do not put a token in the clone URL, shell history, root
account, or deployment config. Obtain `RELEASE_COMMIT` from the repository
owner's signed-off handoff/release record after the release suite passes; do
not substitute the current `main` tip or a short SHA.

Choose `3) Raspberry Pi / Linux`. The installer runs the same preflight again
before invoking `sudo`, apt, npm, systemd, or nginx.

Run `bash deploy/deploy.sh --help` at any time for the non-mutating command
reference. It distinguishes read-only preflight/status operations from
credential, policy, rollback, maintenance-recovery, and internal update
commands.

When prompted:

1. Enter the professor's OpenAI API key twice. Input is hidden.
2. Copy the generated browser access token when it is shown. It is shown only
   once and should be pasted into the site's access prompt.
3. Press Enter at the Cloudflare question to keep the site local-only.
4. Press Enter at the automatic-update question to leave updates disabled.

The installer creates immutable releases under:

```text
/var/www/uconn-isomorphic/releases/<full-commit-sha>/
```

`/var/www/uconn-isomorphic/current` atomically selects the active release.
Uploads, outputs, and workflow state persist under `/var/lib/uconn-isomorphic/`.
The backend runs as the locked `uconn-isomorphic` account, dependency builds run
as the separate locked `uconn-isomorphic-build` account, and completed code,
assets, and virtual environments become root-owned and non-writable by the app
and build accounts. Successful releases carry a root-owned schema-2
`.release-ready` marker. Its immutable digest covers the release tree and built
`site/dist` bytes; the Python virtual environment's bytes are deliberately
excluded because it is rebuilt from the separately hashed requirements lock.
The verifier still checks the virtual environment's types, root ownership,
non-writable modes, and required `uvicorn` executable. Persistent-data symlinks
are checked structurally instead of hashing the mutable data they target.
Frontend `node_modules` are removed after the production build to reduce
SD-card use.

The Pi's current hostname determines the local address; the installer does not
rename the device:

```text
http://<hostname>.local
```

## Device Credentials and Settings

Device-specific credentials and professor-selected runtime policy survive
releases and rollbacks in:

```text
/etc/uconn-isomorphic/environment
```

The file is `0600 root:root` and is the canonical device configuration; a
release-local `site/.env` is optional and normally absent. Systemd loads any
release defaults first and this stable device file second, so device values win.
The production service
refuses to start unless both `OPENAI_API_KEY` and `APP_ACCESS_TOKEN` are
nonempty. Do not copy the file into the repository, display it in logs, or send
its contents to another person.

Non-secret deployment paths and feature choices live as allowlisted data in
`/etc/uconn-isomorphic/deploy.conf` (`0644 root:root`). The installer never
sources this file as shell code. Do not replace it with the old checkout-local
`.deploy.env` pattern.

Configure missing credentials again:

```bash
bash deploy/deploy.sh --configure-secrets
```

Credential configuration/rotation takes the shared deployment lock and refuses
to restart while a workflow is active. Wait for all workflow and batch runs to
finish before using these commands.

Configure bounded request rates, upload/output retention, workflow retention,
concurrency, and maximum generated-question count without opening the secret
file:

```bash
bash deploy/deploy.sh --configure-runtime-policy
```

This command validates every number against a conservative range, writes all
changes atomically under the deployment lock, refuses the change unless the
active-work count is exactly zero, restarts once, and health-checks the result.

Rotate only the OpenAI key:

```bash
bash deploy/deploy.sh --rotate-openai-key
```

Rotate the browser token:

```bash
bash deploy/deploy.sh --rotate-access-token
```

Token rotation invalidates existing browser sessions. The replacement is shown
once. The frontend keeps the token in browser `sessionStorage` and sends it as
the exact `X-App-Access-Token` header. The protected `/api/readiness` endpoint
verifies access. Only these GET endpoints are intentionally public:

- `/api/health`
- `/api/openai/status`
- `/api/public-config`
- `/version.json`

The browser token is not a substitute for Cloudflare Access, per-user quotas,
or OpenAI project spending limits.

### PayPal donation link

Use a PayPal-hosted donation URL rather than putting payment secrets in the
browser. Run the idle-only configuration command:

```bash
bash deploy/deploy.sh --configure-donation
```

Paste a hosted `https://paypal.com`, PayPal subdomain, or `https://paypal.me`
URL. The helper validates it, updates the root-owned environment atomically,
takes the deployment lock, refuses to restart during an active workflow, and
health-checks the backend. Enter a blank value to hide the link. It never asks
for or stores a PayPal REST client secret.

## Local-Only and Optional Public Hosting

Local-only is the default and safest professor handoff. Do not forward router
port 80 to the Pi. The `.local` address uses HTTP, so use it only on a trusted
isolated LAN: the shared browser token and uploaded course files otherwise
travel in plaintext. Do not use `.local` on an untrusted guest, dorm, classroom,
or campus network. Supported access on an untrusted network is either
Cloudflare HTTPS plus Cloudflare Access or an encrypted SSH tunnel from a
trusted computer:

```bash
read -r -p 'SSH target for the Pi (for example professor@192.0.2.10): ' PI_SSH_TARGET
ssh -L 8080:127.0.0.1:80 "${PI_SSH_TARGET}"
# While that SSH session remains open, browse to:
http://127.0.0.1:8080
```

The browser URL is locally HTTP, but the network hop to the Pi is inside SSH.
Do not expose port 80 with router forwarding.

Cloudflare setup runs only after an explicit `y` or `yes`. It installs
`cloudflared` from Cloudflare's signed apt repository, authenticates the
account, selects or creates a tunnel, routes a hostname, and starts
`uconn-isomorphic-cloudflared.service`. It asks for a second explicit
confirmation that Cloudflare Access already protects the exact hostname before
creating/running the public route. It separately requires confirmation that
Cloudflare **Always Use HTTPS** is enabled, excludes the public HTTP origin
from CORS, and refuses to keep the tunnel enabled unless an external HTTP
request redirects to the exact HTTPS hostname. It bypasses local proxy
environment variables for this check, validates the HTTPS certificate, and
requires an anonymous Access denial/login response before reporting success.
The tunnel runs as the locked
`uconn-cloudflared` account with empty Linux capabilities, a read-only system,
private temporary/device views, and access only to its `0600` tunnel credential.
Before enabling it:

1. Enable **Always Use HTTPS** for the hostname's Cloudflare zone.
2. Configure one Cloudflare Access self-hosted application with an empty Path
   so it covers the entire hostname. Inventory and remove any more-specific
   application or Bypass policy that could exempt `/api/`.
3. Restrict access to the professor and intended users.
4. Set OpenAI project budgets and usage alerts.
5. Confirm the selected DNS record and tunnel are dedicated to this app.

The installer uses `/usr/bin/curl -q` with proxies disabled, exact parsed
redirect targets, certificate validation, and anonymous Access probes for both
`/` and `/api/readiness`; it keeps the tunnel disabled if any check fails.
After setup, repeat an incognito-browser check from a separate network: both
the UI and `/api/readiness` must stop at Cloudflare Access before reaching the
application. Public mode binds nginx only to `127.0.0.1:80`, so direct
connections to the Pi's LAN/WAN IP on TCP/80 must also fail; Cloudflare Tunnel
needs no inbound router port-forward.

Answering no on a later installer run disables the existing local cloudflared
service and clears the saved public-host selection. It does not delete remote
Cloudflare DNS/tunnel objects; remove those separately in Cloudflare if they
will not be reused.

The nginx layer forwards `X-App-Access-Token`, limits expensive workflow-start,
upload, and ZIP-download requests to six per minute per client with a small
burst, separately limits public provider-status polling, caps concurrent API
connections, bounds uploads at 64 MiB, and emits a restrictive Content Security
Policy plus baseline security headers.
When traffic arrives through the loopback-only Cloudflare tunnel, nginx trusts
only Cloudflare's `CF-Connecting-IP` header from that loopback peer, so one
remote user cannot consume every user's rate-limit bucket. API request bodies
stream to the authenticated backend so rejected multipart requests are not
first buffered to Pi storage. Ordinary API responses are buffered and capped
at five minutes; only the authenticated
workflow event stream keeps its one-hour streaming timeout. These controls
supplement rather than replace authenticated application quotas.

## Admin Dashboard and Operations Probe

The installer creates a persistent `/var/lib/uconn-isomorphic/config/admin-runtime.env`
override; this is where a dashboard key rotation survives immutable release
switches. The systemd unit loads the optional release-local `site/.env` first (normally absent), this override
second, and the root-owned `/etc/uconn-isomorphic/environment` last, so device
secrets still outrank both. Keep `OPENAI_ADMIN_KEY`, SMTP settings, the
Cloudflare Access audience/team domain, and the admin email allowlist only in
Pi-side files, never in a client bundle.

The backend systemd unit explicitly sets `ADMIN_LOCAL_DEV_MODE=false` after
loading every environment file. This prevents the localhost dashboard test
setting (commented out in the `site/.env.example` template and meant only for a
developer's `site/.env.local`) from becoming a production origin bypass.

It also enables `uconn-isomorphic-operations.timer`, which runs every minute as
a separate systemd job and writes a sanitized report consumed by `/admin`.
The report contains service/tunnel state, disk/load values, public-health
reachability, Cloudflare status, and bounded redacted journal excerpts—not shell
access, credentials, or unrestricted logs.
Configure Cloudflare Access with Google sign-in for `/admin*` and `/api/admin/*`;
the FastAPI service verifies each signed Access assertion again. The tunnel
installer creates the public route only; it does **not** create the Cloudflare
Access application, its Google identity provider, or its allow policy.

## Automatic Updates

Automatic updates are also default-off and require explicit `y` or `yes`.
When enabled:

- `uconn-isomorphic-update.service` runs the root-owned installed updater at
  `/usr/local/libexec/uconn-isomorphic/deploy.sh`, never a script from the
  professor-writable checkout.
- The updater parses `/etc/uconn-isomorphic/deploy.conf` as allowlisted data;
  it never executes it.
- `uconn-isomorphic-update.timer` checks the root-configured, pinned GitHub
  main branch about every 30 minutes,
  with up to five minutes of randomized delay to reduce needless Pi network and
  SD-card wear.
- A candidate is reused only when its schema-2 `.release-ready` marker, commit
  metadata, requirements/package-lock hashes, immutable release digest
  (including `site/dist`), recursively root-owned non-writable tree,
  structurally checked virtual environment, and exact persistent links
  validate.
- Python dependencies install as the unprivileged build account with lockfile
  hashes required; npm uses `npm ci` as that same account.
- Before a selector switch, a root-owned activation transaction is fsynced
  under `/var/lib/uconn-isomorphic-deploy/`. A failed or interrupted activation
  health-checks the previous release before reopening nginx.
- A failed commit stays quarantined until the pinned main branch changes.
- Before activation, nginx enters maintenance mode, the updater checks again,
  and activation is deferred unless the server reports exactly zero active
  workflows. Exit/signal cleanup removes the maintenance marker only after a
  selected release is verified healthy.
- A same-commit check still verifies release integrity and live health; an
  inactive service is restarted only after those checks pass.

The repository owner must have noninteractive, read-only Git access to the
remote, must be a normal non-root account, and should own a checkout under its
ordinary home directory. Enable this only after the repository's test gate is
trusted:

```bash
git ls-remote --exit-code origin refs/heads/main
systemctl list-timers 'uconn-isomorphic-update*'
sudo systemctl status uconn-isomorphic-update.timer
sudo journalctl -u uconn-isomorphic-update.service -f
sudo systemctl disable --now uconn-isomorphic-update.timer
sudo systemctl start uconn-isomorphic-update.service
```

The installed updater intentionally does not replace itself from the pinned
main branch. The separate installed backup/restore helper is refreshed from a
release only after that exact release passes marker, ownership, and live-health
verification. During disaster recovery, the restore child never replaces the
helper that owns its transaction; the parent refreshes it atomically from the
verified active release only after removing the completed restore journal.
After deployment logic changes, review the checkout and rerun
`bash deploy/deploy.sh` interactively, answering `y` to the automatic-update
prompt, to install the reviewed updater version. Pressing Enter at that prompt
disables the timer and does not refresh the installed updater.
If a transient dependency/network failure quarantines an otherwise valid
commit, rerunning the interactive installer from that reviewed commit retries
the isolated build; automatic retries remain suppressed to avoid repeated cost
and SD-card churn.

## Status, Rollback, and Backup

Inspect the sanitized service/release state:

```bash
bash deploy/deploy.sh --status
```

A code rollback selects a previously validated immutable release. It does not
restore SQLite state, uploads, outputs, credentials, or reverse a data migration.
List only eligible full-SHA releases and choose one deliberately:

```bash
find /var/www/uconn-isomorphic/releases -mindepth 1 -maxdepth 1 \
  -type d -regextype posix-extended \
  -regex '.*/[0-9a-f]{40}' -printf '%f\n' | sort
read -r -p 'Validated 40-hex rollback commit: ' ROLLBACK_COMMIT
bash deploy/deploy.sh --rollback "${ROLLBACK_COMMIT}"
```

Manual rollback disables the automatic-update timer and persists the operator
hold so the pinned branch cannot immediately redeploy the newer commit. Review
and rerun the interactive installer before re-enabling updates.

Backup and disaster recovery use the executable, tested state machine in
[BACKUP_RESTORE.md](BACKUP_RESTORE.md). The short command surface is:

```bash
sudo /usr/local/libexec/uconn-isomorphic-backup-restore backup \
  --destination /absolute/luks-mounted/backup-directory
sudo /usr/local/libexec/uconn-isomorphic-backup-restore restore \
  --archive /absolute/backup.tgz \
  --checksum /absolute/backup.tgz.sha256 \
  --checkout "$(pwd -P)" \
  --commit "${RESTORE_COMMIT}"
sudo /usr/local/libexec/uconn-isomorphic-backup-restore recover
sudo /usr/local/libexec/uconn-isomorphic-backup-restore install
sudo /usr/local/libexec/uconn-isomorphic-backup-restore rollback
sudo /usr/local/libexec/uconn-isomorphic-backup-restore status
# Only after accepting the physical restore drill:
sudo /usr/local/libexec/uconn-isomorphic-backup-restore finalize \
  --transaction-id "${RESTORE_TRANSACTION_ID}"
```

Do not replace these commands with a direct `tar` extraction. The helper owns
the deployment lock, conflicting paid-work lease, exact-zero quiescence,
root-only archive staging, strict config/environment validation, per-root
journal, recovery, browser-token rotation, and exact-commit health gate. Each
schema-2 archive carries a verified Git bundle for its recorded commit and
reachable history, so restore installs from an immutable root-owned snapshot
instead of fetching mutable source. Both backup destination and restore source
are refused unless the archive/checksum share a root-owned directory on a
separately mounted Linux filesystem with a verifiable LUKS dm-crypt ancestor.
Backup publication is checksum-first and non-overwriting, not an atomic two-file
transaction. `finalize` is the explicit irreversible step that removes only
marker-matched pre-restore roots after acceptance. A physical spare-SD restore
drill remains required before handoff.

The installer also creates
`/run/uconn-isomorphic-work-lock/activity.lock` without replacing an already
safe inode and installs
`/etc/tmpfiles.d/uconn-isomorphic-work-lock.conf` so systemd recreates the
root-owned `0755` directory and single-link `0444` file after reboot. Paid
web/CLI work takes a shared lease. A mutating command publishes maintenance
first, then waits for the exclusive lease, keeps it through restart and health
verification, removes maintenance, and finally releases it. Do not manually
delete, replace, link, or chmod this file.

## Local Preview

Hot reload:

```bash
node deploy/preview.mjs --dev
```

Built preview:

```bash
node deploy/preview.mjs --web
```

The cross-platform preview launcher is also used by the VS Code `Run Dev Mode`
and `Run Web Preview` tasks. It uses `localhost`, starts the backend,
installs missing local dependencies into the project-local `site/.venv`,
and warns if `OPENAI_API_KEY` is missing. It does not edit the hosts file or
other files outside the repo.

Both modes start a fresh backend, so a preview never serves stale Python code.
Their frontend behavior differs:

- `--web` runs `vite build` (which empties `site/dist/` first) on every invocation,
  then starts a brand-new backend. If the preferred web port is occupied, the
  cross-platform launcher increments until it finds a free port and reports the
  selected URL without stopping the existing process.
- `--dev` starts a fresh backend with `--reload` on the first available backend
  port, then launches Vite with `--force` on the first available dev port so
  dependencies are re-optimized from scratch. Source files are served fresh via
  HMR.

## Useful Checks

```bash
bash deploy/deploy.sh --status
sudo systemctl status uconn-isomorphic
sudo systemctl status nginx
sudo systemctl status uconn-isomorphic-cloudflared
sudo systemctl status uconn-isomorphic-update.timer
sudo systemctl status uconn-isomorphic-operations.timer
sudo journalctl -u uconn-isomorphic -f
sudo journalctl -u uconn-isomorphic-cloudflared -f
sudo journalctl -u uconn-isomorphic-update.service -f
sudo stat -c '%a %U %G %n' /etc/uconn-isomorphic/environment
sudo stat -c '%a %U %G %n' /etc/uconn-isomorphic/deploy.conf
sudo stat -c '%a %U %G %n' /var/lib/uconn-isomorphic-deploy/deployment_status.json
sudo systemctl cat uconn-isomorphic | grep -F 'EnvironmentFile='
sudo systemctl cat uconn-isomorphic-update | grep -F 'ExecStart='
sudo systemd-analyze verify /etc/systemd/system/uconn-isomorphic.service
sudo nginx -t
readlink -f /var/www/uconn-isomorphic/current
backend_port="$(sed -n 's/^BACKEND_PORT=//p' /etc/uconn-isomorphic/deploy.conf)"
/usr/bin/curl -q --noproxy '*' --proto '=http' \
  --connect-timeout 2 --max-time 5 -fsS \
  "http://127.0.0.1:${backend_port}/api/health"
/usr/bin/curl -q --noproxy '*' --proto '=http' \
  --connect-timeout 2 --max-time 5 -fsS \
  "http://127.0.0.1:${backend_port}/version.json"
```

Expected credential-file metadata is `600 root root`; `deploy.conf` and the
sanitized deployment-status file are root-owned and non-writable by the app.
The backend unit should run as `uconn-isomorphic`, while the updater `ExecStart`
should point into `/usr/local/libexec/uconn-isomorphic/`. The systemd output should
show the release environment first and
`/etc/uconn-isomorphic/environment` second. These commands report paths and
configured state only; they do not print credential values.

If the backend fails with `error while attempting to bind on address`, check
what is already listening on the deploy backend port:

```bash
backend_port="$(sed -n 's/^BACKEND_PORT=//p' /etc/uconn-isomorphic/deploy.conf)"
sudo ss -ltnp "sport = :${backend_port}"
```

The deploy script stops stale `uconn-isomorphic` backend processes automatically,
but it will not kill an unrelated process. Stop the conflicting process or edit
the root-owned data-only config:

```bash
sudoedit /etc/uconn-isomorphic/deploy.conf
# change BACKEND_PORT=3002 to another unused port, then:
bash deploy/deploy.sh
```
