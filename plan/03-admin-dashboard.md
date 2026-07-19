# 03 — Admin Dashboard: Architecture & Feature Spec

The professor-facing control center. This doc is the umbrella spec; each subsystem has
a dedicated doc (auth, site controls, history, ratings, budget) linked inline.

---

## 1. What it must do (from the request)

| Capability | Doc |
|---|---|
| Sign in with a username/password the professor sets | [04-auth-and-security.md](04-auth-and-security.md) |
| Enable/disable features | §3 below (feature flags) |
| Check site status / backend status; live vs local | [05-site-controls.md](05-site-controls.md) |
| Take the site online/offline; bring it back | [05-site-controls.md](05-site-controls.md) |
| View domain + Cloudflare info | [05-site-controls.md](05-site-controls.md) |
| View completed workflows by day/time/user/ip | [06-workflow-history-and-users.md](06-workflow-history-and-users.md) |
| Rate outputs good/bad, saved into workflow data | [07-ratings.md](07-ratings.md) |
| Limit requests/tokens per user per day; predictable cost | [08-rate-limiting-and-budget.md](08-rate-limiting-and-budget.md) |

---

## 2. Architecture at a glance

```
Browser (SPA)
  └─ mode === 'admin'  ──►  <AdminDashboard/>  (client gate = UX only)
        │  fetch /api/admin/*  (session cookie)
        ▼
FastAPI  api/admin.py   [Depends(require_admin)]   ← the real boundary
        ├─ status/deploy-info   → systemctl is-active, .deploy.env  (read-only)
        ├─ host controls        → enum→argv via narrow sudoers      (audited)
        ├─ history/users        → new usage_ledger table            (queryable)
        ├─ ratings              → workflow record + ledger
        ├─ feature flags        → runtime_flags table (live-editable)
        └─ budget/limits        → usage_ledger aggregates + kill-switch
        ▼
SQLite (site/backend/runtime/)  — new tables alongside the existing records blob
```

**Design principles**
- **Server-side authorization is the boundary.** The `mode==='admin'` client gate is
  cosmetic (frontend review H3); every `/api/admin/*` route enforces `require_admin`.
- **Additive, not invasive.** New router (`api/admin.py`), new tables, new frontend mode
  — reuse existing patterns (router style from `config.py`, `ui/` primitives, the
  registry-driven reducer). Don't refactor the pipeline.
- **Read-only first, mutation gated + audited.** Status/history/visibility ship first
  and are low-risk; host mutation and config changes are opt-in, audited, and
  privilege-scoped.
- **Graceful on dev/Windows.** systemd/`.deploy.env`/cloudflared exist only on the Pi;
  the dashboard degrades cleanly locally (see [05](05-site-controls.md) §3).

---

## 3. Feature flags (the "enable/disable features" ask)

A small runtime key/value store the admin edits live, read by both backend and frontend.

### Store
New table `runtime_flags(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT, updated_by TEXT)`.
Seeded from env defaults on first boot; env is the floor, the table overrides at runtime
(so a flag can be flipped without a redeploy). A tiny lazy accessor
(`flags.get(key, default)`) with a short in-process cache + invalidation on write.

### Flags to ship
| Flag | Effect | Default |
|---|---|---|
| `maintenance_mode` | Serve a banner; reject new generations (soft "offline") | off |
| `generation_enabled` | Master switch for all workflow starts | on |
| `evaluation_enabled` | Allow the pricey eval stage on the student path | off |
| `create_data_enabled` | Gate the batch Create-Data flow | on (dev) |
| `uploads_enabled` | Allow user uploads vs bundled-only | on |
| `signups_open` / `roster_locked` | If/when identity is roster-based | — |
| budget knobs (`user_daily_limit`, `global_daily_cost_limit`, `semester_budget_usd`, …) | see [08](08-rate-limiting-and-budget.md) | env |

### Wiring
- Backend: a `require_feature("generation_enabled")` dependency on the start routes; the
  budget gate reads the numeric flags.
- Frontend: expose read-only flag state via a new `GET /api/config/flags` (public subset
  — e.g. `maintenance_mode`, `generation_enabled`) so the SPA can show a maintenance
  banner and disable the composer. Admin-only flags stay behind `/api/admin/flags`.
- Every write → `admin_audit` row.

---

## 4. Frontend composition

- Add `admin` to `SITE_MODES` ([SiteChrome.jsx:15-19](../site/frontend/src/components/SiteChrome.jsx#L15-L19));
  branch in [App.jsx:209-222](../site/frontend/src/App.jsx#L209-L222) to render
  `<AdminDashboard/>` instead of `ChatConversation`, mirroring the dev-aside swap.
- **But** the `admin` option should only appear after a successful `/api/admin/login`
  (probe `GET /api/admin/me`); otherwise the dashboard shows a login card. The mode
  entry is convenience; auth is the gate.
- `AdminDashboard` = `Tabs` over panels, each composed from `ui/` primitives:
  - **Overview** — status tiles (`StatusChip`/`StatusDot`), live/local badge, version,
    OpenAI status, today's spend + semester burn-down gauges.
  - **Controls** — maintenance toggle, online/offline, restart, auto-update pause
    (`ToggleSwitch`, `Button`), all confirm-gated.
  - **Feature Flags** — `ToggleSwitch` list bound to `runtime_flags`.
  - **Workflows** — `DataTable` with day/time/user/ip/status/tokens/cost/rating columns,
    filters, drill-in (see [06](06-workflow-history-and-users.md)).
  - **Budget** — per-user usage, limit config, kill-switch state (see [08](08-rate-limiting-and-budget.md)).
  - **Audit** — `admin_audit` log table.
- Wrap the whole dashboard in an `ErrorBoundary` (frontend review H1).
- Reuse the HTTP client `utils/api.js`; do **not** hand-roll `fetch`.

---

## 5. New backend surface (summary)

All under `api/admin.py`, `require_admin` (except login):

```
POST /api/admin/login            POST /api/admin/logout      GET  /api/admin/me
GET  /api/admin/status           GET  /api/admin/deploy-info
POST /api/admin/controls/{action}   # enum: maintenance_on/off, offline/online, restart, autoupdate_on/off
GET  /api/admin/flags            PUT  /api/admin/flags/{key}
GET  /api/admin/workflows        GET  /api/admin/workflows/{id}
GET  /api/admin/users            GET  /api/admin/usage        # aggregates for budget
GET  /api/admin/audit
```
Plus a small **public** `GET /api/config/flags` (safe subset) and the user-facing
`POST /api/workflow/{id}/rating` (see [07](07-ratings.md)).

---

## 6. Dependencies / ordering

The dashboard sits on top of foundational backend work that must land first:
1. **Auth** ([04](04-auth-and-security.md)) — nothing is safe without it.
2. **Identity capture** (backend E-1) — attribute runs to a user/ip.
3. **Store schema + timezone fix** (backend H-2, E-4) — queryable, correct history.
4. **Usage capture** (budget §4.1) — tokens/cost for budget + history columns.

Then the dashboard panels are largely presentation over those primitives. Full sequence
in [09-implementation-roadmap.md](09-implementation-roadmap.md).
