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

## Deployment Shape

The scripts deploy into this layout:

- source clone: wherever you cloned the Git repo, commonly `~/Scripts/UConn-IsomorphicQuestionLLM`
- runtime copy: `/var/www/uconn`
- frontend root: `/var/www/uconn/dist`
- backend service: `uconn.service`
- backend bind: `127.0.0.1:3002`
- nginx public port: `80`
- Cloudflare tunnel service: `cloudflared.service`
- default hostname: `uconn.lindimismaili.com`
- default tunnel name: `pi-tunnel`

nginx serves the React build from `/var/www/uconn/dist` and reverse-proxies
`/api/` to Uvicorn.

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

On first run, the script may print a Cloudflare login URL. Open it, log in,
select the domain, then return to the terminal.

## What `deploy.sh` Runs

`deploy.sh` calls these scripts in order:

1. `deploy/cloudflare.sh`
   Installs `cloudflared`, authenticates, creates or reuses the `pi-tunnel`
   tunnel, routes the hostname to the tunnel, installs `cloudflared.service`,
   and rsyncs the repo to `/var/www/uconn`.

2. `deploy/packages.sh`
   Installs system packages, creates `/var/www/uconn/.venv`, installs backend
   dependencies from `backend/requirements.txt`, installs frontend dependencies,
   builds `dist/`, and writes `dist/version.json`.

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

This checks `origin/main`, pulls with `--ff-only`, rebuilds frontend assets,
syncs to `/var/www/uconn`, restarts `uconn.service`, and reloads nginx.

## Automatic Git Pull Every Minute

This is already implemented in `deploy/sync.sh` as a systemd timer.

Install it:

```bash
cd ~/Scripts/UConn-IsomorphicQuestionLLM
bash deploy/sync.sh --install-timer
```

The timer runs every 60 seconds. It fetches `origin/main`; if the remote commit
differs from the local commit, it pulls, rebuilds, deploys, and restarts the
backend.

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

- `OPENAI_API_KEY` missing: create `/var/www/uconn/.env` or repo `.env`, then run `bash deploy/sync.sh` and restart `sudo systemctl restart uconn`.
- Backend not running: `sudo journalctl -u uconn -n 100`.
- nginx config failed: `sudo nginx -t`.
- Cloudflare tunnel not reachable: `sudo journalctl -u cloudflared -n 100`.
- Auto-sync not firing: `systemctl status uconn-sync.timer` and `sudo journalctl -u uconn-sync -n 100`.
- Git pull fails: make sure the source clone is clean and has credentials that allow pulling from the repo.
