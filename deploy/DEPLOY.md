# Raspberry Pi Deployment

This folder now has two scripts:

- `deploy.sh` installs and configures the Raspberry Pi runtime.
- `preview.sh` runs self-contained local previews at `isomorphic.localhost`.

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

The script installs system packages, confirms Python, creates `/var/www/uconn-isomorphic/.venv`, installs backend requirements, confirms or installs Node/npm, builds the Vite site, configures nginx, installs the backend systemd service, and sets the Pi hostname/local address so the site is available at:

```text
http://isomorphic.local
```

It warns if `.env` is missing or does not contain `OPENAI_API_KEY`.

After local verification, the script asks whether to attempt Cloudflare hosting. Press Enter to continue, or any other key to skip. If continued, it installs `cloudflared`, runs `cloudflared tunnel login`, creates or reuses a tunnel, copies tunnel credentials into `/etc/cloudflared`, writes the tunnel config, routes DNS, and starts `cloudflared.service`.

The default tunnel name is:

```text
uconn_isomorphic
```

## Local Preview

Hot reload:

```bash
bash deploy/preview.sh --dev
```

Built preview:

```bash
bash deploy/preview.sh --web
```

The preview script uses `isomorphic.localhost`, starts the backend, installs missing local dependencies into the active/project virtual environment, and warns if `OPENAI_API_KEY` is missing. It does not edit `/etc/hosts` or other files outside the repo.

## Useful Checks

```bash
sudo systemctl status uconn-isomorphic
sudo systemctl status nginx
sudo systemctl status cloudflared
sudo journalctl -u uconn-isomorphic -f
sudo journalctl -u cloudflared -f
```
