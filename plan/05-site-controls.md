# 05 — Site & Backend Controls (status, live/local, online/offline, domain, Cloudflare)

> The professor wants to: check site status & backend status, see whether the site is
> **live vs local**, take it **online/offline** (and bring it back), see the **domain**
> it's hosted on, and view **Cloudflare** info. This doc maps each to what exists and
> what to build, and — importantly — flags what a web app can and cannot safely do to
> its own host.

---

## 0. What already exists (reuse it)

- `GET /api/health` — healthy flag, OpenAI-key-configured, upload/output dir
  writability ([health.py:27](../site/backend/src/api/health.py#L27)).
- `GET /api/openai/status` — upstream OpenAI outage indicator.
- `GET /version.json` — deployed commit SHA / publish time.
- systemd is the process manager. Units:
  **`uconn-isomorphic`** (backend, uvicorn @ `127.0.0.1:3002`, user `www-data`),
  **`nginx`** (serves `dist/`, proxies `/api/`), **`uconn-isomorphic-cloudflared`**
  (public tunnel), **`uconn-isomorphic-update.timer`** (auto-update).
- **`.deploy.env`** (gitignored, `600`) holds `PUBLIC_HOSTNAME`, `LOCAL_DOMAIN`,
  `LOCAL_HOSTNAME`, `SERVICE_NAME`, `BACKEND_PORT`, `TUNNEL_NAME`, `TUNNEL_UUID`,
  `AUTO_UPDATE`. **This is the authoritative source of domain/tunnel facts.**
- Cloudflare config: `/etc/cloudflared/uconn-isomorphic.yml` (`644`); credential JSON
  is `600` (never expose it).

**Key limitation:** the backend only ever sees `127.0.0.1` — **it does not
intrinsically know its own public URL**. Public identity comes from `.deploy.env`,
not from the request. So "am I live?" is answered by reading `.deploy.env` + probing
the tunnel, not by introspection.

---

## 1. Feature-by-feature mapping

### 1a. Site status / backend status — **SAFE, build first**
New read-only `GET /api/admin/status` (behind `require_admin`) that returns:

```jsonc
{
  "backend":   { "unit": "uconn-isomorphic",            "active": true, "since": "..." },
  "nginx":     { "unit": "nginx",                        "active": true },
  "cloudflared": { "unit": "uconn-isomorphic-cloudflared","active": true },
  "autoUpdate": { "unit": "uconn-isomorphic-update.timer","active": false },
  "health":    { /* proxied GET /api/health */ },
  "openai":    { /* proxied GET /api/openai/status */ },
  "version":   { /* version.json */ }
}
```

Implementation: `systemctl is-active <unit>` (read-only, no sudo needed) via
`subprocess` with a fixed arg list (never shell=True, never user input). Cache for a
few seconds to avoid hammering systemd on dashboard polls.

### 1b. Live vs local — **SAFE**
Derive from `.deploy.env` + a probe:
- If `PUBLIC_HOSTNAME` is set **and** `uconn-isomorphic-cloudflared` is active **and**
  an outbound `GET https://{PUBLIC_HOSTNAME}/api/health` succeeds → **LIVE** (publicly
  reachable). Show the public URL.
- Else → **LOCAL only** (reachable at `http://{LOCAL_DOMAIN}` / `127.0.0.1`), show the
  LAN address.
- Surface both addresses and the probe result + timestamp.

### 1c. Domain & Cloudflare info — **SAFE (display-only, redacted)**
`GET /api/admin/deploy-info` reads `.deploy.env` and the cloudflared `.yml`:
- Public hostname, local domain/hostname, backend port, service names.
- Tunnel **name** and **UUID last-4 only** (never the full UUID or credential file).
- Auto-update on/off, deployed commit.
- Never read or return `/etc/cloudflared/*.json` (the secret credential).

### 1d. Online / offline toggle — **RISKY, build carefully**
"Offline" and "online" should mean **public reachability**, not killing the app:
- **Take offline (public):** `systemctl stop uconn-isomorphic-cloudflared`. The
  public tunnel drops; LAN/nginx and the API keep running. Clean and reversible; the
  admin (on LAN or via a still-open session) can bring it back.
- **Bring online:** `systemctl start uconn-isomorphic-cloudflared`.
- **Optional "maintenance mode" (softer):** a feature flag (see
  [03-admin-dashboard.md](03-admin-dashboard.md)) that makes the app serve a
  maintenance banner and reject new generations, **without** touching systemd — safest
  of all, and doesn't lock the professor out. Recommend offering **both**: a soft
  maintenance flag (default control) and the hard cloudflared stop (advanced).

**Do NOT** implement "offline" as stopping `uconn-isomorphic` (the backend) — the
request that stops it would kill the very process trying to respond, and nothing local
could restart it from the web. Restarting/stopping the **backend itself** must be
delegated (see 1e).

### 1e. Restart backend — **RISKY, delegate**
A request handler cannot cleanly restart its own uvicorn. Two safe patterns:
- **`systemd-run` one-shot / transient unit:** the handler launches a detached
  `systemd-run --on-active=2s systemctl restart uconn-isomorphic`, returns
  immediately, then the restart happens after the response is sent.
- **Dedicated oneshot unit** `uconn-isomorphic-restart.service` that the handler
  `systemctl start`s (fire-and-forget).
- Poll `/api/admin/status` afterward to confirm it came back healthy.

### 1f. Auto-update pause/resume — **SAFE-ish**
`systemctl disable --now uconn-isomorphic-update.timer` / `enable --now`. Useful so a
redeploy doesn't wipe a live demo mid-class.

---

## 2. Privilege model (the crux)

The backend runs as **`www-data`**, which cannot `systemctl start/stop/restart`
without rights. Grant the **minimum**:

```
# /etc/sudoers.d/uconn-isomorphic-admin  (installed by deploy.sh, mode 440, visudo-checked)
www-data ALL=(root) NOPASSWD: /usr/bin/systemctl start uconn-isomorphic-cloudflared, \
  /usr/bin/systemctl stop uconn-isomorphic-cloudflared, \
  /usr/bin/systemctl start uconn-isomorphic-update.timer, \
  /usr/bin/systemctl stop uconn-isomorphic-update.timer, \
  /usr/bin/systemctl disable uconn-isomorphic-update.timer, \
  /usr/bin/systemctl enable uconn-isomorphic-update.timer, \
  /usr/bin/systemctl start uconn-isomorphic-restart.service
```

Rules:
- **Exact commands only** — no wildcards, no `systemctl *`, no shell. Each allowed
  invocation is a full literal command line.
- Read-only `is-active`/`is-enabled` do **not** need sudo — keep them out of the
  allowlist.
- Every mutating call goes through one Python wrapper that maps a small **enum of
  actions** → a fixed argv; the HTTP layer never passes strings into the command.
- All mutations require `require_admin` **and** write an `admin_audit` row.
- Make the whole host-control surface **opt-in** via an env flag
  (`ADMIN_HOST_CONTROLS=1`), off by default, so a misconfigured box can't be poked.

---

## 3. Windows/dev note

`systemctl`, `.deploy.env`, and cloudflared exist **only on the deployed Pi**. On the
dev machine (Windows) these controls must **degrade gracefully**: `/api/admin/status`
reports `unsupported_platform` for the systemd tiles, the toggle/restart buttons show
as disabled with an explanatory tooltip, and `deploy-info` returns whatever
`.deploy.env` exists or an empty/dev placeholder. Guard every `subprocess`/systemd
path behind a platform check so the dashboard still renders in local dev.

---

## 4. Acceptance criteria

- Status tiles reflect real `systemctl is-active` for all four units.
- Live/local badge matches reality (verified by toggling cloudflared).
- Domain/Cloudflare panel shows public host + redacted tunnel, never the credential.
- "Take offline" stops the tunnel and the public URL stops resolving; "bring online"
  restores it; the professor is never locked out (LAN + session persist).
- Restart returns promptly and the status poll shows the backend healthy again.
- On Windows/dev, controls disable cleanly with no crashes.
- Every mutation is audit-logged; sudoers grants nothing beyond the enumerated units.

---

*Cross-refs: auth & sudoers rationale → [04-auth-and-security.md](04-auth-and-security.md);
feature flags / maintenance mode / dashboard shell → [03-admin-dashboard.md](03-admin-dashboard.md);
deploy.sh changes → [09-implementation-roadmap.md](09-implementation-roadmap.md).*
