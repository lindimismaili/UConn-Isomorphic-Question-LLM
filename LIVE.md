# Raspberry Pi + Cloudflare Tunnel + Nginx Setup

This document is a **from‑scratch, command‑by‑command rebuild guide** for hosting websites on a Raspberry Pi using **Cloudflare Tunnel** and **Nginx**, with:

- `lindimismaili.com` as the main site
- an example subdomain: `uconn.lindimismaili.com`

No router port forwarding. HTTPS handled by Cloudflare.

---

## Assumptions

- Raspberry Pi OS
- User: `triforcelindim`
- Domain already managed by Cloudflare
- Site source repos live under `~/Scripts/` and are kept up to date via GitHub:
  - `git@github.com:lindimismaili/LindimsWebsite.git` → `~/Scripts/LindimsWebsite`
  - `git@github.com:lindimismaili/UConn-IsomorphicQuestionLLM.git` → `~/Scripts/UConn-IsomorphicQuestionLLM`
- Static deploy copies are synced into `/var/www/` via `rsync`:
  - Main site: `~/Scripts/LindimsWebsite/` → `/var/www/lindimismaili/`
  - UConn subdomain: `~/Scripts/UConn-IsomorphicQuestionLLM/Site/` → `/var/www/uconn/`

---

## STEP 1 — System Prep

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y curl gnupg lsb-release rsync nginx git
```

---

## STEP 1.5 — Clone Site Repos into `~/Scripts`

```bash
mkdir -p /home/triforcelindim/Scripts

cd /home/triforcelindim/Scripts

# Main site
if [ -d LindimsWebsite/.git ]; then
  (cd LindimsWebsite && git pull --ff-only)
else
  git clone git@github.com:lindimismaili/LindimsWebsite.git
fi

# UConn app repo (static site is in ./Site)
if [ -d UConn-IsomorphicQuestionLLM/.git ]; then
  (cd UConn-IsomorphicQuestionLLM && git pull --ff-only)
else
  git clone git@github.com:lindimismaili/UConn-IsomorphicQuestionLLM.git
fi
```

---

## STEP 2 — Install cloudflared

```bash
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg \
 | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null

echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] \
https://pkg.cloudflare.com/cloudflared stable main" \
 | sudo tee /etc/apt/sources.list.d/cloudflared.list

sudo apt update
sudo apt install cloudflared -y
cloudflared --version
```

---

## STEP 3 — Authenticate with Cloudflare (NO sudo)

```bash
cloudflared tunnel login
```

This opens a browser. Log in to Cloudflare and select `lindimismaili.com`.

Creates:

```
~/.cloudflared/cert.pem
```

---

## STEP 4 — Create Tunnel

```bash
cloudflared tunnel create pi-tunnel
```

Creates:

```
~/.cloudflared/${UUID}.json
```

Capture the UUID for later steps:

```bash
UUID="$(cloudflared tunnel list | awk '$2 == \"pi-tunnel\" { print $1; exit }')"
echo "UUID=$UUID"
test -n "$UUID"
```

---

## STEP 5 — Move Tunnel Files to System Location

```bash
sudo mkdir -p /etc/cloudflared
sudo cp ~/.cloudflared/${UUID}.json /etc/cloudflared/
sudo cp ~/.cloudflared/cert.pem /etc/cloudflared/

sudo chown root:root /etc/cloudflared/*
sudo chmod 600 /etc/cloudflared/*.json
sudo chmod 600 /etc/cloudflared/cert.pem
```

---

## STEP 6 — Create cloudflared Config

```bash
sudo nano /etc/cloudflared/config.yml
```

```yaml
tunnel: pi-tunnel
credentials-file: /etc/cloudflared/${UUID}.json

ingress:
  - hostname: lindimismaili.com
    service: http://localhost:3000

  - hostname: www.lindimismaili.com
    service: http://localhost:3000

  - service: http_status:404
```

---

## STEP 7 — Create cloudflared systemd Service

```bash
sudo nano /etc/systemd/system/cloudflared.service
```

```ini
[Unit]
Description=cloudflared
After=network-online.target
Wants=network-online.target

[Service]
Type=notify
ExecStart=/usr/bin/cloudflared --no-autoupdate --config /etc/cloudflared/config.yml tunnel run
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable cloudflared
```

---

## STEP 8 — Create Cloudflare DNS Routes

```bash
cloudflared tunnel route dns pi-tunnel lindimismaili.com
cloudflared tunnel route dns pi-tunnel www.lindimismaili.com
```

---

## STEP 9 — Set Up Main Site Files

```bash
# Pull latest site sources
cd /home/triforcelindim/Scripts/LindimsWebsite
git pull --ff-only

sudo mkdir -p /var/www/lindimismaili
sudo rsync -av --delete /home/triforcelindim/Scripts/LindimsWebsite/ /var/www/lindimismaili/

sudo chown -R www-data:www-data /var/www/lindimismaili
sudo find /var/www/lindimismaili -type d -exec chmod 755 {} \;
sudo find /var/www/lindimismaili -type f -exec chmod 644 {} \;
```

---

## STEP 10 — Nginx Config for Main Domain

```bash
sudo nano /etc/nginx/sites-available/lindimismaili
```

```nginx
server {
    listen 3000;
    server_name lindimismaili.com www.lindimismaili.com;

    root /var/www/lindimismaili;
    index index.html;

    client_max_body_size 100M;

    location / {
        try_files $uri $uri/ =404;
    }
}
```

```bash
sudo ln -sf /etc/nginx/sites-available/lindimismaili /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default

sudo nginx -t
sudo systemctl restart nginx
```

---

## STEP 11 — Start cloudflared

```bash
sudo systemctl restart cloudflared
sudo systemctl status cloudflared
```

Test:

```bash
curl -I http://localhost:3000
curl -I https://lindimismaili.com
```

---

# ADDING A SUBDOMAIN (Example: uconn.lindimismaili.com)

---

## STEP A — Prepare Subdomain Files

```bash
# Pull latest site sources
cd /home/triforcelindim/Scripts/UConn-IsomorphicQuestionLLM
git pull --ff-only

sudo mkdir -p /var/www/uconn
sudo rsync -av --delete /home/triforcelindim/Scripts/UConn-IsomorphicQuestionLLM/Site/ /var/www/uconn/

sudo chown -R www-data:www-data /var/www/uconn
sudo find /var/www/uconn -type d -exec chmod 755 {} \;
sudo find /var/www/uconn -type f -exec chmod 644 {} \;
```

---

## STEP B — Nginx Config for Subdomain

```bash
sudo nano /etc/nginx/sites-available/uconn
```

```nginx
server {
    listen 3001;
    server_name uconn.lindimismaili.com;

    root /var/www/uconn;
    index index.html;

    client_max_body_size 100M;

    location / {
        try_files $uri $uri/ =404;
    }
}
```

```bash
sudo ln -sf /etc/nginx/sites-available/uconn /etc/nginx/sites-enabled/uconn
sudo nginx -t
sudo systemctl reload nginx
```

Test:

```bash
curl -I http://localhost:3001
```

---

## STEP C — Add Subdomain to cloudflared

```bash
sudo nano /etc/cloudflared/config.yml
```

Add **above** the final 404 rule:

```yaml
- hostname: uconn.lindimismaili.com
  service: http://localhost:3001
```

```bash
sudo systemctl restart cloudflared
```

---

## STEP D — Create DNS Route for Subdomain

```bash
cloudflared tunnel route dns pi-tunnel uconn.lindimismaili.com
```

Test:

```bash
curl -I https://uconn.lindimismaili.com
```

---

## Final Architecture

```
Browser (HTTPS)
→ Cloudflare Edge
→ Cloudflare Tunnel
→ cloudflared (Pi)
→ Nginx (localhost:3000 / 3001)
→ Static files in /var/www/
```

No port forwarding.
No public IP exposure.
HTTPS fully handled by Cloudflare.

