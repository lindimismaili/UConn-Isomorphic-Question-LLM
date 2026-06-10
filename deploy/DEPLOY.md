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

The script installs system packages, confirms Python, creates `/var/www/uconn-isomorphic/.venv`, installs backend requirements, confirms or installs Node/npm, builds the Vite site, configures nginx, and installs the backend systemd service. It does **not** rename the Pi. The local address uses the device's current hostname:

```text
http://<hostname>.local
```

It warns if `.env` is missing or does not contain `OPENAI_API_KEY`.

After local verification, the script asks whether to attempt Cloudflare hosting. Press Enter to continue, or any other key to skip. If continued, it installs `cloudflared`, runs `cloudflared tunnel login`, discovers active tunnels in the Cloudflare account, reuses the saved tunnel from `.deploy.env` when it still exists, or automatically uses the only active tunnel found. If several active tunnels exist, the script lists them and prompts for the tunnel name, defaulting to the first existing tunnel. It then copies tunnel credentials into `/etc/cloudflared`, writes the tunnel config, routes DNS, and starts `cloudflared.service`.

If no active tunnel is found, the default tunnel name is:

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

Both modes always rebuild the frontend **and start a fresh backend**, so a
preview never serves stale assets or stale Python code:

- `--web` runs `vite build` (which empties `dist/` first) on every invocation,
  then starts a brand-new backend. If the app's own backend is already running
  on the web port it is stopped first and relaunched on freshly built assets; an
  unrelated process on that port is left alone and the preview moves to the next
  free port instead.
- `--dev` likewise stops any existing app backend and starts a fresh one (with
  `--reload`), and launches Vite with `--force` so dependencies are re-optimized
  from scratch (ignoring the `node_modules/.vite` cache). Source files are always
  served fresh via HMR.

Stopping only ever targets a process that answers the app's `/api/health` check,
so it restarts the app's own backend rather than killing an unrelated process.

## Useful Checks

```bash
sudo systemctl status uconn-isomorphic
sudo systemctl status nginx
sudo systemctl status cloudflared
sudo journalctl -u uconn-isomorphic -f
sudo journalctl -u cloudflared -f
```
