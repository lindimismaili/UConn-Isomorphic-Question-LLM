# Raspberry Pi Deployment And Admin Dashboard

How the app is deployed and operated on the Raspberry Pi, and how the protected
`/admin` operations dashboard works. Read this before deploying, exposing the
site publicly, or testing the dashboard locally. Step-by-step runbooks live in
[deploy/HANDOFF.md](../deploy/HANDOFF.md),
[deploy/DEPLOY.md](../deploy/DEPLOY.md), and
[deploy/BACKUP_RESTORE.md](../deploy/BACKUP_RESTORE.md).

## Raspberry Pi Automatic Deployment

Use a supported **64-bit Raspberry Pi OS** installation; verify the device
reports an `aarch64` kernel and `arm64` packages. The preflight requires
Python 3.10+, at least 1,700 MiB RAM (a 4 GB-class Pi is recommended), and at
least 8 GiB free on both the release and persistent-data filesystems. Run
`bash deploy/deploy.sh --preflight` before installation. The first deployment
should remain local. Do not enable public Cloudflare access until
`APP_ACCESS_TOKEN`, header rejection/acceptance, rate limiting, download
protection, a manual backup, Cloudflare Access, and an HTTP-to-HTTPS redirect
have been verified.

The Pi deployer treats the root-configured, pinned GitHub remote's `main`
branch as the only automatic deployment source. It fetches that ref without
merging, builds the commit in an isolated release directory, then atomically
changes the `current` release symlink only after the candidate is ready. The
prior three known-good releases remain on disk for rollback.

- User data survives every deployment under the persistent `uploads/`,
  `outputs/`, and `runtime/` directories. This includes generated questions,
  evaluations, uploaded files, and workflow-history SQLite state.
- A failed fetch, build, restart, or health check leaves the existing release
  running or restores it automatically. The failed commit is quarantined until
  the pinned `main` ref changes, so the default 30-minute timer (plus up to five
  minutes of jitter) does not rebuild a known-bad commit forever.
- `GET /api/deployment/status` exposes a sanitized summary and recent bounded
  deployment events for the operations dashboard. Detailed diagnostics remain
  in `journalctl -u uconn-isomorphic-update.service`.
- Device secrets are stored outside releases in the root-owned
  `/etc/uconn-isomorphic/environment`; release `.env` files contain only
  non-secret defaults.
- Backups are operator-triggered through the root-owned
  `uconn-isomorphic-backup-restore` helper. It captures persistent SQLite data,
  uploads, outputs, deployment state, and secrets only onto a separately
  mounted Linux filesystem with a verified LUKS dm-crypt ancestor. Schema-2
  archives include a verified bundle of the exact Git commit and reachable
  history. Restore requires the archive/checksum to stay together on equivalent
  verified LUKS storage, rotates the browser token, and retains marker-bound
  pre-restore roots until the operator explicitly finalizes the accepted
  transaction; immutable release rollback remains code-only.
- On an installed Pi, every web/CLI paid start holds a shared lease on the
  root-owned `/run/uconn-isomorphic-work-lock/activity.lock` through provider
  and artifact cleanup. A systemd-tmpfiles rule recreates its exact root-owned
  directory/file ABI after reboot without replacing a safe live inode. Deploy,
  backup, and restore publish maintenance, take the conflicting exclusive
  lease, and keep it through restart, health verification, and maintenance
  removal, preventing a race between accepted paid work and quiescence.
- The installer also enables the minute-by-minute operations probe, which feeds
  sanitized Pi, tunnel, disk, public-health, and Cloudflare status into `/admin`.

## Secure Operations Dashboard

`/admin` is a protected operations dashboard for archive, uploads, real output
folders, evaluation artifacts, selected prompts/configuration files, workflow
history, maintenance/cache controls, health reports, and incident/audit history.
Its Overview refreshes every 30 seconds without reloading the page and reports
site reachability, Cloudflare/tunnel state, OpenAI status, active visitors,
workflows, storage, and incidents. Configure Cloudflare Access to protect
`/admin*` and `/api/admin/*` with Google sign-in, then set
`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, and a comma-separated `ADMIN_EMAILS`
allowlist only on the Pi. The server validates the signed Access assertion
itself; a Cloudflare page rule alone is not sufficient.

The file browser displays canonical logical paths, such as
`/site/backend/outputs`. Its `Prompts & configuration` root exposes only
`data/prompts/*.txt`, `data/agents.json`, `data/latex_template.tex`, and
`data/rubric.json`; it does not expose arbitrary data files, runtime databases,
or credentials.

The dashboard stores non-secret state in `runtime/operations.sqlite3`. The Pi
installer creates a release-independent `admin-runtime.env` override for an
admin-replaced `OPENAI_API_KEY` and optional `OPENAI_ADMIN_KEY`; neither is
returned through the API. Optional SMTP alert settings are `SMTP_HOST`,
`SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, and
`ALERT_RECIPIENTS`. The operations timer writes a sanitized Pi/cloudflared
report every minute. The tunnel installer does not create a Cloudflare Access
application or Google policy; configure those in Cloudflare Zero Trust after
the public hostname is available.

## Local Dashboard Testing

`site/.env.example` includes a commented-out, non-production dashboard test
identity so you can exercise the full dashboard without Cloudflare. To turn it
on, uncomment both lines in your own `site/.env.local` (never in the committed
template):

```env
ADMIN_LOCAL_DEV_MODE=true
ADMIN_LOCAL_DEV_EMAIL=local-admin@example.test
```

Start the site bound to `127.0.0.1`/`localhost`, then visit
`http://localhost:8000/admin`. This bypass is accepted only from a direct
loopback connection and only while both variables are set; it must never be
placed in the Pi runtime configuration. It exercises the real local file,
workflow, maintenance, cache, and download controls. Leave it off when running
`deploy/preview.mjs --lan`: the Vite dev proxy forwards LAN requests to the
backend from `127.0.0.1`, so other devices would pass the loopback check.

The Pi systemd service explicitly sets `ADMIN_LOCAL_DEV_MODE=false` after
loading every environment file, so the local test setting can never bypass
Cloudflare Access in production.

## Before Pushing To Main

Before pushing to `main`, test changes that affect startup, dependencies,
database/runtime compatibility, uploads, or output artifacts. Runtime-data
changes must be backward compatible with the prior release or include an
explicit migration and rollback path; automatic deployment can roll back code,
but it cannot safely undo destructive data migrations.
