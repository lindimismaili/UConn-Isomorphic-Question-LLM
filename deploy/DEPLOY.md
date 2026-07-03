# Raspberry Pi Deployment

This folder has three launchers:

- `deploy.sh` installs and configures the Raspberry Pi runtime.
- `preview.mjs` runs cross-platform local previews at `localhost` on Windows,
  macOS, and Linux.
- `preview.sh` remains available as the Unix shell preview launcher.

## Raspberry Pi Install

From a cloned repo on Raspberry Pi OS:

```bash
bash deploy/deploy.sh
```

The script first asks which platform you are on:

```text
1) Mac
2) Windows
3) Raspberry Pi / Linux
```

Choose `3` to continue with the full installer. Mac and Windows selections exit with local development guidance because the deploy path depends on apt, nginx, systemd, and avahi.

The script installs system packages, confirms Python, creates `/var/www/uconn-isomorphic/.venv`, installs backend requirements, confirms or installs Node/npm, builds the Vite site, configures nginx, and installs the backend systemd service. It does **not** rename the Pi. The local address uses the device's current hostname:

```text
http://<hostname>.local
```

It warns if `.env` is missing or does not contain `OPENAI_API_KEY`.

After local verification, the script asks whether to attempt Cloudflare hosting. Press Enter to continue, or any other key to skip. If continued, it installs `cloudflared`, runs `cloudflared tunnel login`, discovers active tunnels in the Cloudflare account, reuses the saved tunnel from `.deploy.env` when it still exists, or automatically uses the only active tunnel found. If several active tunnels exist, the script lists them and prompts for the tunnel name, defaulting to the first existing tunnel. It then copies tunnel credentials into `/etc/cloudflared`, writes an app-scoped tunnel config, routes DNS, and starts `uconn-isomorphic-cloudflared.service` by default.

When Cloudflare hosting is enabled for a hostname, the installer treats that
hostname as belonging to this app. It disables enabled nginx sites and
cloudflared services/configs that already claim the same hostname or tunnel
UUID, then rewrites DNS with `cloudflared tunnel route dns --overwrite-dns` if
needed.

If no active tunnel is found, the default tunnel name is:

```text
uconn_isomorphic
```

## Automatic Updates

After the Cloudflare step, the installer asks whether to enable automatic
updates. Press Enter to enable, any other key to skip. The choice is saved to
`.deploy.env` as `AUTO_UPDATE`, and declining on a later run disables it again.

When enabled, the installer creates two systemd units:

- `uconn-isomorphic-update.service` — a `oneshot` that runs
  `deploy/deploy.sh --update`.
- `uconn-isomorphic-update.timer` — fires 60s after boot and every 60s after the
  previous run finishes (a long redeploy can never overlap itself).

Each cycle does a `git fetch` of `origin/main` and compares commits; if nothing
changed it exits immediately with no rebuild. When `main` has moved it
**force-redeploys**: the checkout is reset to exactly match `origin/main`
(`checkout -f` + `reset --hard` + `clean -ffd`, so **any local edits to tracked
files are discarded**), then it re-syncs to the deploy directory, rebuilds the
frontend, and restarts the backend (waiting on `/api/health`).
`dist/version.json` reflects the new short commit SHA once a redeploy completes.

Your data is preserved. `clean` runs without `-x`, so gitignored paths
(`uploads/`, `outputs/`, `runtime/`) are left alone — and those dirs are also
listed as explicit `clean` excludes as a safeguard. More importantly, the live
uploaded/generated data lives under the deploy directory
(`/var/www/uconn-isomorphic/...`), which git never touches; the redeploy's
`rsync` already excludes `uploads/`, `outputs/`, and `runtime/` there too.

**Prerequisite:** the timer pulls non-interactively **as the repository owner**
(not root), so that user must have working git auth for `origin`. With the
default SSH remote (`git@github.com:...`) that means a passphrase-less deploy key
on the Pi; alternatively use an HTTPS remote with cached credentials.

Manage it with:

```bash
systemctl list-timers 'uconn-isomorphic-update*'
sudo systemctl status uconn-isomorphic-update.timer
sudo journalctl -u uconn-isomorphic-update.service -f
sudo systemctl disable --now uconn-isomorphic-update.timer   # turn off
sudo systemctl start uconn-isomorphic-update.service          # update now, once
```

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
installs missing local dependencies into the active/project virtual environment,
and warns if `OPENAI_API_KEY` is missing. It does not edit the hosts file or
other files outside the repo.

Both modes always rebuild the frontend **and start a fresh backend**, so a
preview never serves stale assets or stale Python code:

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
sudo systemctl status uconn-isomorphic
sudo systemctl status nginx
sudo systemctl status uconn-isomorphic-cloudflared
sudo systemctl status uconn-isomorphic-update.timer
sudo journalctl -u uconn-isomorphic -f
sudo journalctl -u uconn-isomorphic-cloudflared -f
sudo journalctl -u uconn-isomorphic-update.service -f
```

If the backend fails with `error while attempting to bind on address`, check
what is already listening on the deploy backend port:

```bash
sudo ss -ltnp 'sport = :3002'
```

The deploy script stops stale `uconn-isomorphic` backend processes automatically,
but it will not kill an unrelated process. Stop the conflicting process or run
the installer with a different backend port, for example:

```bash
BACKEND_PORT=3003 bash deploy/deploy.sh
```
