# Raspberry Pi Deployment Runbook

This runbook is for installing the app on a brand new Raspberry Pi and serving
it with nginx, Uvicorn, and optionally Cloudflare Tunnel.

The short version:

```bash
sudo apt update
sudo apt install -y git curl
mkdir -p ~/Scripts
cd ~/Scripts
git clone <repo-url> UConn-IsomorphicQuestionLLM
cd UConn-IsomorphicQuestionLLM
printf 'OPENAI_API_KEY=your_openai_key_here\n' > .env
bash deploy/deploy.sh
```

`deploy/deploy.sh` is the recommended path. It installs Cloudflare Tunnel,
system packages, Python dependencies, frontend dependencies, nginx, and the
backend systemd service.

The frontend build uses Vite 7. Raspberry Pi images that still have Node 18
will fail with `crypto.hash is not a function` or a Vite Node version error.
The deploy scripts check Node before building and install Node.js 22 from
NodeSource when the current version is missing or too old.
Node 21 is not in Vite's allowed range; use Node 20.19+ within Node 20.x, or
Node 22.12+.

NodeSource DEB packages support `amd64`, `armhf` for ARMv7 and newer, and
`arm64`. Very old ARMv6 Raspberry Pis cannot use this NodeSource path; use a
supported Raspberry Pi OS architecture or build the frontend on another machine
and deploy the resulting `dist/`.

The deploy script prompts for the public site target:

- full site name, for example `example.com`
- deploy dir name, for example `uconn`
- subdomain name, for example `uconn`

With those examples, the app is deployed to `/var/www/uconn` and served at
`uconn.example.com`. Use `@` as the subdomain to serve the root domain,
`example.com`.

## Deployment Shape

The scripts deploy into this layout:

- source clone: wherever you cloned the Git repo, commonly `~/Scripts/UConn-IsomorphicQuestionLLM`
- runtime copy: `/var/www/<deploy-dir-name>`
- frontend root: `/var/www/<deploy-dir-name>/dist`
- backend service: `uconn.service`
- backend bind: `127.0.0.1:3002`
- nginx public port: `80`
- Cloudflare tunnel service: `cloudflared.service`
- hostname: `<subdomain>.<site-name>`
- default tunnel name: `pi-tunnel`

nginx serves the React build from the configured deploy directory's `dist/`
folder and reverse-proxies `/api/` to Uvicorn.

## Fresh Install Steps

1. Install the minimal tools needed to clone and run the deploy script:

```bash
sudo apt update
sudo apt install -y git curl
```

2. Clone the repository:

```bash
mkdir -p ~/Scripts
cd ~/Scripts
git clone <repo-url> UConn-IsomorphicQuestionLLM
cd UConn-IsomorphicQuestionLLM
```

3. Add runtime secrets:

```bash
cat > .env <<'EOF'
OPENAI_API_KEY=your_openai_key_here
EOF
```

If the Cloudflare DNS record already exists and you want the deploy script to
update it automatically, add a Cloudflare API token with DNS edit permission:

```bash
cat >> .env <<'EOF'
CLOUDFLARE_API_TOKEN=your_cloudflare_dns_edit_token_here
EOF
```

4. Run the deploy:

```bash
bash deploy/deploy.sh
```

Answer the prompts. For `uconn.example.com`, use:

```text
What is your full site name? Example: example.com [lindimismaili.com]: example.com
What is the deploy dir name? Example: uconn [uconn]: uconn
What is the subdomain name? Example: uconn, use @ for the root domain [uconn]: uconn
```

The script writes those answers to `.deploy.env`, which is intentionally ignored
by git. `deploy/sync.sh`, `deploy/cloudflare.sh`, `deploy/packages.sh`, and
`deploy/live.sh` read that file on later runs.

On first run, the script may print a Cloudflare login URL. Open it, log in,
select the domain, then return to the terminal.

## What `deploy.sh` Runs

`deploy.sh` calls these scripts in order:

1. `deploy/cloudflare.sh`
   Installs `cloudflared`, authenticates, creates or reuses the `pi-tunnel`
   tunnel, routes the hostname to the tunnel, installs `cloudflared.service`,
   and rsyncs the repo to the configured deploy directory.

2. `deploy/packages.sh`
   Installs system packages, ensures Node satisfies Vite's Node requirement,
   creates the deploy directory `.venv`, installs backend dependencies from
   `backend/requirements.txt`, installs frontend dependencies, builds `dist/`,
   and writes `dist/version.json`.

3. `deploy/live.sh`
   Writes `/etc/nginx/sites-available/uconn`, enables nginx, writes
   `/etc/systemd/system/uconn.service`, and starts/restarts the backend.

4. `deploy/sync.sh --install-timer` when you answer yes to the prompt.
   Installs automatic one-minute update checks.

## Verify The Deployment

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
sudo journalctl -u cloudflared -f
sudo journalctl -u nginx -n 50
```

## Updating The Pi

Manual update from the source clone:

```bash
cd ~/Scripts/UConn-IsomorphicQuestionLLM
bash deploy/sync.sh
```

This reads `.deploy.env`, checks `origin/main`, pulls with `--ff-only`,
rebuilds frontend assets, syncs to the configured `/var/www/<deploy-dir-name>`,
restarts `uconn.service`, and reloads nginx.

If the build failed because Node is `v18.x`, upgrade from the source clone:

```bash
cd ~/Scripts/UConn-IsomorphicQuestionLLM
git pull --ff-only
bash deploy/packages.sh
node -v
npm -v
bash deploy/sync.sh
```

`node -v` should print `v22.12+` or another version that satisfies Vite's
Node 20.19+ within 20.x or Node 22.12+ requirement.

## Automatic Git Pull Every Minute

This is already implemented in `deploy/sync.sh` as a systemd timer.

Install it:

```bash
cd ~/Scripts/UConn-IsomorphicQuestionLLM
bash deploy/sync.sh --install-timer
```

The timer runs every 60 seconds and uses the values saved in `.deploy.env`. It
fetches `origin/main`; if the remote commit differs from the local commit, it
pulls, rebuilds, deploys, and restarts the backend.

Check status:

```bash
systemctl status uconn-sync.timer
systemctl list-timers --all | grep uconn-sync
sudo journalctl -u uconn-sync -f
```

Remove it:

```bash
bash deploy/sync.sh --remove-timer
```

## Non-Cloudflare Option

If you do not want Cloudflare Tunnel, use the direct Raspberry Pi deploy script:

```bash
bash deploy/raspi-deploy.sh
```

That installs system packages, builds the frontend, syncs to `/var/www/uconn`,
creates the destination Python venv, writes nginx config, and installs
`uconn.service`. It does not install or configure `cloudflared`.

## Troubleshooting

- `OPENAI_API_KEY` missing: create `.env` in the source clone or in the configured deploy directory, then run `bash deploy/sync.sh` and restart `sudo systemctl restart uconn`.
- Backend not running: `sudo journalctl -u uconn -n 100`.
- nginx config failed: `sudo nginx -t`.
- Cloudflare tunnel not reachable: `sudo journalctl -u cloudflared -n 100`.
- Auto-sync not firing: `systemctl status uconn-sync.timer` and `sudo journalctl -u uconn-sync -n 100`.
- Git pull fails: make sure the source clone is clean and has credentials that allow pulling from the repo.
- Vite fails with `crypto.hash is not a function`: Node is too old. Run `git pull --ff-only`, then `bash deploy/packages.sh`, then confirm `node -v` is `v22.12+` or compatible.
