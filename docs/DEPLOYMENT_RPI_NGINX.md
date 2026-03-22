# Raspberry Pi + Nginx (static) + Uvicorn (API on localhost:3001)

Assumption: this repo lives at `/var/www/uconn` and you want:

- Frontend served by **nginx** from `/var/www/uconn/Site`
- Backend API served by **uvicorn** on `127.0.0.1:3001`
- nginx reverse-proxies `/api/*` → `http://127.0.0.1:3001`

## 1) Install OS packages

```bash
sudo apt update
sudo apt install -y nginx python3 python3-venv python3-pip
```

## 2) Put the project in `/var/www/uconn`

```bash
sudo mkdir -p /var/www/uconn
sudo chown -R "$USER":"$USER" /var/www/uconn

cd /var/www/uconn
# git clone <your-repo-url> .
```

## 3) Create a venv + install Python deps (no global installs)

```bash
cd /var/www/uconn
python3 -m venv .venv
./.venv/bin/pip install -r backend/requirements.txt
```

## 4) Configure env vars (OpenAI key)

Create `/var/www/uconn/.env`:

```bash
OPENAI_API_KEY=YOUR_KEY_HERE
```

## 5) systemd service (uvicorn on 127.0.0.1:3001)

Create `/etc/systemd/system/uconn.service`:

```ini
[Unit]
Description=UConn Isomorphic Question LLM (FastAPI)
After=network.target

[Service]
Type=simple
WorkingDirectory=/var/www/uconn
Environment=PORT=3001
Environment=HOST=127.0.0.1
ExecStart=/var/www/uconn/.venv/bin/uvicorn src.server:app --host 127.0.0.1 --port 3001
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable + start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now uconn
sudo systemctl status uconn --no-pager
```

## 6) nginx site (serve static + proxy /api)

Create `/etc/nginx/sites-available/uconn`:

```nginx
server {
    listen 80;
    server_name uconn.lindimismaili.com;

    root /var/www/uconn/Site;
    index index.html;

    client_max_body_size 50m;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /api/ {
        proxy_pass http://127.0.0.1:3001;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE / long-running requests
        proxy_read_timeout 3600;
        proxy_send_timeout 3600;
        proxy_buffering off;
    }
}
```

Enable the site:

```bash
sudo ln -sf /etc/nginx/sites-available/uconn /etc/nginx/sites-enabled/uconn
sudo nginx -t
sudo systemctl reload nginx
```

## 7) Test locally on the Pi

```bash
curl -i http://127.0.0.1/api/sliders
curl -i http://127.0.0.1/
```

## 8) Cloudflare DNS for `uconn.lindimismaili.com`

In Cloudflare DNS for `lindimismaili.com`:

- If your Pi is publicly reachable: create an `A` record `uconn` → your public IP (and forward ports 80/443 to the Pi).
- If your ISP blocks inbound ports: use Cloudflare Tunnel instead.

### Cloudflare Tunnel (recommended for home internet)

Use `cloudflared` to publish your local nginx:

- Public hostname: `uconn.lindimismaili.com`
- Service: `http://localhost:80`

This avoids router port-forwarding.
