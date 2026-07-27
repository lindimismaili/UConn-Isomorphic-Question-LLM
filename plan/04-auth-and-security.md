# 04 — Admin Authentication & Security

> **Status — proposed architecture, not current authentication; status warning added
> July 27, 2026.** The current release uses an optional shared
> `APP_ACCESS_TOKEN`, not the professor account/session system below. See
> [`SECURITY.md`](../SECURITY.md) for the implemented boundary.

> Professor signs in with a username/password **he** sets. This doc specifies how
> that login works given the repo's realities, plus the security hardening the admin
> surface forces us to confront.

---

## 0. Hard constraints discovered in the codebase

1. **The current release has an optional shared-token boundary, not professor
   authentication.** `APP_ACCESS_TOKEN` protects API routes when configured,
   but it does not establish a professor identity, role, session, or per-user
   authorization. The account/session architecture proposed below remains
   unimplemented.
2. **A secret-bearing `site/.env` has historically been tracked.** The current
   runtime loader ([bootstrap.py](../site/backend/src/bootstrap.py) `load_dotenv`)
   loads ignored `site/.env.local` before tracked non-secret defaults, while
   process/systemd values win. Do not infer that any historical value is live;
   rotate any key that was ever committed and keep active values in ignored or
   server-only configuration.
   → **Therefore the admin password must NOT live in `site/.env`.** Committing it
   would publish it. It belongs in the systemd unit's `Environment=` /
   `EnvironmentFile=` pointing at a `600`-perm file **outside** the git checkout.
3. **CORS is `allow_credentials=False`** ([server.py:64](../site/backend/src/server.py#L64)).
   Cookie-based cross-origin auth requires flipping this to `True` **and** pinning
   `allow_origins` to the exact domain (you cannot combine `True` with `*`). In the
   deployed topology the browser and API are same-origin (both behind nginx on the
   public host), so this is a non-issue in prod but matters for the Vite dev proxy.
4. Env values are applied with `os.environ.setdefault` — **existing env wins over
   `.env`** — so systemd `Environment=ADMIN_...` cleanly overrides anything.

---

## 1. Where admin credentials live

**Recommended (prod):** values written by `deploy.sh` to the existing
root-owned mode-`0600` `/etc/uconn-isomorphic/environment` file, which systemd
already loads outside the Git checkout:

```ini
# added to install_backend_service() heredoc in deploy.sh
Environment=ADMIN_USERNAME=%i          # professor picks this
Environment=ADMIN_PASSWORD_HASH=...     # bcrypt/argon2 hash, NOT plaintext
Environment=SESSION_SECRET=...          # 32+ random bytes for cookie signing
```

- Store a **hash**, not the plaintext password. Use `passlib[bcrypt]` (add to
  requirements) or stdlib `hashlib.scrypt`. `deploy.sh` prompts for the password
  once, hashes it, and writes only the hash.
- `SESSION_SECRET` is generated once (`openssl rand -hex 32`) and persisted in
  `/etc/uconn-isomorphic/environment` so sessions survive restarts.
- **Local dev:** allow the same vars via `.env.local` support (add `.env.local` to the
  loader, gitignored) **or** just export them in the shell. Document that the admin
  area is disabled if `ADMIN_USERNAME`/`ADMIN_PASSWORD_HASH` are unset.

---

## 2. Auth mechanism — recommendation

Given **single-professor usage behind an HTTPS Cloudflare tunnel**, keep it simple
and robust. Two viable options:

### Option A — Signed session cookie (recommended)
- `POST /api/admin/login` (username + password form) → verify with
  `secrets.compare_digest` on username and `passlib.verify` on the hash → set an
  **HttpOnly, Secure, SameSite=Strict** signed cookie (`itsdangerous` or Starlette
  `SessionMiddleware`) with a short TTL (e.g. 8h) + sliding refresh.
- `POST /api/admin/logout` clears it.
- A `require_admin` dependency validates the cookie on every `/api/admin/*` route.
- **Pros:** real login UX, logout, expiry, no re-prompt each request. Best fit for a
  dashboard the professor keeps open.

### Option B — HTTP Basic (simplest)
- FastAPI `HTTPBasic` dependency; browser shows the native login dialog.
- Constant-time compare of username + password hash.
- **Pros:** ~20 lines, no cookie machinery. **Cons:** clumsy logout, credentials
  re-sent every request, no session concept.

**Verdict:** ship **Option A**. It's the difference between "a tool" and "a dialog
box," and the dashboard is stateful (feature toggles, live status) so a session model
fits. Fall back to Basic only if we want the absolute minimum for a first spike.

### Login hardening
- **Rate-limit** `/api/admin/login` (e.g. 5 attempts / 15 min / IP) and add a small
  fixed delay on failure to blunt brute force. Log every attempt with IP + timestamp
  to the audit log (§4).
- Constant-time comparisons everywhere (`secrets.compare_digest`, `passlib`).
- Generic error copy ("invalid username or password") — never reveal which was wrong.

---

## 3. Router wiring

- New `site/backend/src/api/admin.py` — an `APIRouter(prefix="/api/admin",
  dependencies=[Depends(require_admin)])` (login/logout excluded from the
  dependency). Register it in [server.py](../site/backend/src/server.py) **before**
  the static mount (API routes already win over the `StaticFiles` catch-all).
- Mirror the existing router style in
  [config.py](../site/backend/src/api/config.py) — thin handlers, shared state/helpers
  in a new `admin_state.py` or extended `state.py`.
- Frontend admin routes are gated in the SPA (see
  [03-admin-dashboard.md](03-admin-dashboard.md)); server-side `require_admin` is the
  real boundary — the client gate is only UX.

---

## 4. Security items the admin surface forces (do not skip)

| # | Issue | Action |
|---|---|---|
| S1 | A tracked secret-bearing `.env` can expose an OpenAI key. | Rotate any key that was ever committed, keep active values in ignored/server-only configuration, and assess history cleanup if the repository is or becomes public. Never claim a historical value is currently live without verifying it securely. |
| S2 | Admin endpoints may read allowlisted deployment status/config and fixed `systemctl` state | Everything stays behind `require_admin`; never return environment files, Cloudflare credentials, raw journals, or arbitrary host paths. |
| S3 | Host mutation (restart/offline) needs elevated rights | Keep the `uconn-isomorphic` app account unprivileged. Use a root-owned, fixed-action oneshot/helper with a narrow authorization boundary; no shell, wildcards, user-supplied unit names, or direct secret-file access. See [05-site-controls.md](05-site-controls.md). |
| S4 | Cookie/session secrets | `HttpOnly`+`Secure`+`SameSite=Strict`; `SESSION_SECRET` ≥ 32 bytes; rotate on demand. |
| S5 | Audit trail | New `admin_audit` ledger row for every admin action (login, toggle, restart, rating override, config change) with `who/when/ip/action/args`. |
| S6 | CORS | Keep `allow_credentials=False` unless cookies must cross origin; in prod the dashboard is same-origin so leave it. If dev needs it, add a dev-only exact-origin + credentials config path. |
| S7 | The dashboard exposes **PII** (student IPs, usage) | Restrict to admin; document retention; consider hashing/truncating IPs at rest (see [06](06-workflow-history-and-users.md)). |

---

## 5. Acceptance criteria

- Unauthenticated request to any `/api/admin/*` (except login) → `401`.
- Wrong password → `401`, rate-limited after N tries, logged.
- Correct login → session cookie; dashboard loads; survives backend restart (secret
  persisted).
- Admin password is **never** present in any git-tracked file.
- `OPENAI_API_KEY` rotated and relocated out of tracked `.env`.

---

*Cross-refs: dashboard shell & client gating → [03-admin-dashboard.md](03-admin-dashboard.md);
host controls & sudoers → [05-site-controls.md](05-site-controls.md); audit/history
tables → [06-workflow-history-and-users.md](06-workflow-history-and-users.md).*
