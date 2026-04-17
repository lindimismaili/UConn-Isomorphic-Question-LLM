# Raspberry Pi Deployment

This folder contains the deployment scripts for running the app on a Raspberry Pi.
The recommended fresh-install path is `deploy.sh`.

## What The Scripts Do

- `deploy.sh` is the main orchestrator. Run it once from a cloned repo on the Pi.
- `cloudflare.sh` installs/configures `cloudflared`, creates or reuses the tunnel, routes the hostname, and syncs the repo to the configured deploy directory.
- `packages.sh` installs system packages, upgrades Node to a Vite-compatible version when needed, creates the deploy directory `.venv`, installs Python dependencies, installs frontend dependencies, and builds `dist/`.
- `live.sh` writes the nginx config and `uconn.service`, then starts/restarts nginx and the FastAPI backend.
- `sync.sh` is the update path. It fetches `origin/main`, pulls fast-forward updates, rebuilds, rsyncs to the configured deploy directory, and restarts services.
- `sync.sh --install-timer` installs a systemd timer that runs `sync.sh` every 60 seconds.
- `raspi-deploy.sh` is the older direct deploy path. Use it only if you do not want the Cloudflare tunnel setup from `deploy.sh`.

## Fresh Raspberry Pi Install

Start on a new Raspberry Pi OS install with SSH/network access.

```bash
sudo apt update
sudo apt install -y git curl
mkdir -p ~/Scripts
cd ~/Scripts
git clone <repo-url> UConn-IsomorphicQuestionLLM
cd UConn-IsomorphicQuestionLLM
```

Create the runtime environment file before deploying:

```bash
cat > .env <<'EOF'
OPENAI_API_KEY=your_openai_key_here
EOF
```

Optional, but useful if the Cloudflare DNS record already exists and the script
needs to overwrite it:

```bash
cat >> .env <<'EOF'
CLOUDFLARE_API_TOKEN=your_cloudflare_dns_edit_token_here
EOF
```

Run the deploy:

```bash
bash deploy/deploy.sh
```

The script asks for:

- full site name, such as `example.com`
- deploy dir name, such as `uconn`, which becomes `/var/www/uconn`
- subdomain name, such as `uconn`, which becomes `uconn.example.com`

Use `@` for the subdomain if you want the root domain, such as `example.com`.
The answers are saved in `.deploy.env` so future syncs and timer runs use the
same target.

The frontend build uses Vite 7, so Node.js 18 is not enough. `deploy.sh`,
`packages.sh`, and the direct `raspi-deploy.sh` path install Node.js 22 from
NodeSource when `node` is missing or older than Vite's required range.
Node 21 is not valid for Vite 7; use Node 20.19+ within Node 20.x, or
Node 22.12+.

NodeSource DEB packages cover `amd64`, `armhf` for ARMv7 and newer, and
`arm64`. Very old ARMv6 Raspberry Pis cannot use this NodeSource path; use
supported hardware/OS architecture or build the frontend elsewhere and deploy
the resulting `dist/`.

During first Cloudflare setup, `cloudflared tunnel login` opens a browser URL.
Log in, choose the Cloudflare zone for the hostname, and return to the terminal.

By default the scripts deploy:

- app directory: `/var/www/<deploy-dir-name>`
- frontend: nginx serving `/var/www/<deploy-dir-name>/dist`
- backend: `uconn.service` running Uvicorn on `127.0.0.1:3002`
- public hostname: `<subdomain>.<site-name>`
- Cloudflare tunnel: `pi-tunnel`

## Verify The Install

```bash
curl -i http://localhost/api/health
curl -i http://localhost/
sudo systemctl status uconn
sudo systemctl status nginx
sudo systemctl status cloudflared
```

Useful logs:

```bash
sudo journalctl -u uconn -f
sudo journalctl -u nginx -n 50
sudo journalctl -u cloudflared -f
```

## Manual Updates

From the repo clone, run:

```bash
bash deploy/sync.sh
```

That command reads `.deploy.env`, fetches `origin/main`, pulls with `--ff-only`,
rebuilds the frontend, syncs the deployed copy, and restarts the backend service. It preserves
deployment-local data such as `.env`, `.venv`, `backend/outputs/`, and
`backend/uploads/`.

If a manual build already failed with `crypto.hash is not a function` or a
message that Vite requires Node 20.19+/22.12+, upgrade the Pi first:

```bash
git pull --ff-only
bash deploy/packages.sh
node -v
npm -v
```

After `node -v` reports `v22.12+` or another compatible version, rerun
`bash deploy/sync.sh` or `bash deploy/deploy.sh`.

## Automatic Updates Every Minute

Yes, this is already supported through a systemd timer:

```bash
bash deploy/sync.sh --install-timer
```

The timer runs `deploy/sync.sh` every 60 seconds with the values from
`.deploy.env`. Each run checks whether `origin/main` has a newer commit. If it
does, the script pulls it, rebuilds, syncs to the configured deploy directory,
restarts `uconn.service`, and reloads nginx.

Check the timer:

```bash
systemctl status uconn-sync.timer
systemctl list-timers --all | grep uconn-sync
sudo journalctl -u uconn-sync -f
```

Remove the timer:

```bash
bash deploy/sync.sh --remove-timer
```

## Non-Cloudflare Local Deploy

If you only need nginx plus the backend service and do not want the Cloudflare
tunnel setup, run:

```bash
bash deploy/raspi-deploy.sh
```

That path still builds the app, syncs `/var/www/uconn`, writes nginx config, and
installs `uconn.service`, but it does not create or run a Cloudflare tunnel.
