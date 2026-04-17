# Raspberry Pi + Nginx (static) + Uvicorn (API on localhost:3002)

Fast path from an already-cloned repo on the Pi:

```bash
cd /path/to/UConn-IsomorphicQuestionLLM
bash deploy/raspi-deploy.sh
```

`deploy/raspi-deploy.sh` now installs missing OS packages (`git`, `rsync`, `nginx`, `python3`, `python3-venv`, `python3-pip`, `nodejs`, `npm`), builds the frontend, syncs the app into `/var/www/uconn`, copies `.env` into place when present, creates the destination venv, and installs nginx + systemd.

Assumption: this repo lives anywhere readable by your user and you want:

- Frontend served by **nginx** from `/var/www/uconn/dist`
- Backend API served by **uvicorn** on `127.0.0.1:3002`
- nginx reverse-proxies `/api/*` → `http://127.0.0.1:3002`

## 1) Clone the repo on the Pi

```bash
mkdir -p /home/triforcelindim/Scripts
cd /home/triforcelindim/Scripts
git clone <your-repo-url>
cd UConn-IsomorphicQuestionLLM
```

## 2) Put your runtime env vars in repo `.env`

```bash
OPENAI_API_KEY=YOUR_KEY_HERE
```

## 3) Run the deploy script

```bash
bash deploy/raspi-deploy.sh
```

The script writes:

- `/etc/nginx/sites-available/uconn`
- `/etc/systemd/system/uconn.service`
- `/var/www/uconn`

## 4) Test locally on the Pi

```bash
curl -i http://127.0.0.1/api/dropdown
curl -i http://127.0.0.1/
```

## 5) Cloudflare DNS for `uconn.lindimismaili.com`

In Cloudflare DNS for `lindimismaili.com`:

- If your Pi is publicly reachable: create an `A` record `uconn` → your public IP (and forward ports 80/443 to the Pi).
- If your ISP blocks inbound ports: use Cloudflare Tunnel instead.

### Cloudflare Tunnel (recommended for home internet)

Use `cloudflared` to publish your local nginx:

- Public hostname: `uconn.lindimismaili.com`
- Service: `http://localhost:80`

This avoids router port-forwarding.
