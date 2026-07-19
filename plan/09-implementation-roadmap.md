# 09 — Implementation Roadmap

Phased plan. Each phase is independently shippable and testable. Phases 0–3 are
**foundational** — the admin dashboard (Phase 4+) is largely presentation on top of them.
Effort is rough (S/M/L) for one developer.

---

## Phase 0 — Safety & correctness fixes (do first, ship on their own)

These are real bugs found in review; several **block** the admin work. Independent of any
new feature.

| # | Task | Ref | Size |
|---|---|---|---|
| 0.1 | **Rotate `OPENAI_API_KEY`**, move it out of git-tracked `site/.env` into systemd `EnvironmentFile` (600, outside git) | [04](04-auth-and-security.md) S4/S1, infra §5 | S |
| 0.2 | Bound upload reads (stream like `files.py`) on `start-from-agent-input` + eval-batch | [01](01-codebase-review.md) C-1 | M |
| 0.3 | Fix timezone parsing (aware UTC) in all TTL/stale/age math; unify timestamp helpers | [01](01-codebase-review.md) H-2 | M |
| 0.4 | Fix cross-thread dict races in the prune loop (lock or loop-thread snapshot) | [01](01-codebase-review.md) H-1 | M |
| 0.5 | Make status GET pure-read; debounce/`to_thread` any heartbeat write | [01](01-codebase-review.md) H-3 | M |
| 0.6 | Eval-batch persistence + distinct store `kind` | [01](01-codebase-review.md) H-4/H-5 | S |
| 0.7 | Frontend: add `ErrorBoundary`; fix "Stop wipes transcript"; delete dead `PromptInput.jsx` | [02](02-ui-ux-review.md) H1/H2/M6 | M |
| 0.8 | Single-workflow `POST /api/workflow/{id}/stop` (also needed by admin) | [01](01-codebase-review.md) M-1 | S |

**Gate:** `run-tests` skill green (backend pytest + ruff, frontend vitest + eslint).

---

## Phase 1 — Admin authentication (the front door)

| # | Task | Ref | Size |
|---|---|---|---|
| 1.1 | `passlib`/scrypt hashing; `deploy.sh` prompts for admin user/password, writes hash + `SESSION_SECRET` to systemd env / `.deploy.env` | [04](04-auth-and-security.md) §1 | M |
| 1.2 | `api/admin.py` router + `require_admin` dependency (session cookie, `SessionMiddleware`) | [04](04-auth-and-security.md) §2/§3 | M |
| 1.3 | `login`/`logout`/`me`; rate-limit + audit failed logins | [04](04-auth-and-security.md) §2 | S |
| 1.4 | `admin_audit` table + helper | [04](04-auth-and-security.md) S5 | S |
| 1.5 | Frontend: `admin` mode appears only after `/me` succeeds; login card; `ErrorBoundary`-wrapped `<AdminDashboard/>` shell | [03](03-admin-dashboard.md) §4 | M |

**Gate:** unauth `/api/admin/*` → 401; login round-trips; secret never in git.

---

## Phase 2 — Identity + queryable store (foundation for history/budget/ratings)

| # | Task | Ref | Size |
|---|---|---|---|
| 2.1 | Capture `request` → validated client IP (trust proxy header) + user identity model **B** (name/email gate) | [06](06-workflow-history-and-users.md) §2/§3 | M |
| 2.2 | `run_ledger` table + `WorkflowStore` query methods (`by_day/user/ip/range` + aggregates) | [06](06-workflow-history-and-users.md) §4 | M |
| 2.3 | Write ledger row at start; reconcile at finish; reconcile local folder-date vs UTC | [06](06-workflow-history-and-users.md) §1/§4 | M |
| 2.4 | Privacy: IP retention policy + student-facing note | [06](06-workflow-history-and-users.md) §2 | S |

---

## Phase 3 — Token/cost accounting + rate limiting + budget

| # | Task | Ref | Size |
|---|---|---|---|
| 3.1 | Capture `RunResult.context_wrapper.usage` in `run_llm_stage`; accumulate per-stage + workflow totals; est. cost from env price table | [08](08-rate-limiting-and-budget.md) §4.1 | M |
| 3.2 | Write tokens/cost into `run_ledger`; add eval-stage usage | [08](08-rate-limiting-and-budget.md) §4.1/4.2 | S |
| 3.3 | `check_and_reserve_budget` pre-flight gate on all start routes (429 + structured body); reservation + reconciliation | [08](08-rate-limiting-and-budget.md) §4.3 | M |
| 3.4 | Per-user daily (tokens+count), global daily cost, **semester kill-switch** | [08](08-rate-limiting-and-budget.md) §4.4/§5 | M |
| 3.5 | Frontend: friendly "limit reached / resets at…" state on the composer | [08](08-rate-limiting-and-budget.md) §4.5 | S |

**Gate:** run a real generation; confirm tokens/cost recorded; caps trip correctly.

---

## Phase 4 — Feature flags + site controls

| # | Task | Ref | Size |
|---|---|---|---|
| 4.1 | `runtime_flags` table + lazy accessor; `require_feature` dep; public `GET /api/config/flags` subset | [03](03-admin-dashboard.md) §3 | M |
| 4.2 | Maintenance mode (soft offline) banner + generation block | [05](05-site-controls.md) 1d | S |
| 4.3 | `GET /api/admin/status` (systemctl is-active ×4, health/openai/version proxy) | [05](05-site-controls.md) 1a | M |
| 4.4 | Live/local badge + `GET /api/admin/deploy-info` (redacted `.deploy.env`/cloudflared) | [05](05-site-controls.md) 1b/1c | M |
| 4.5 | Host mutation: narrow sudoers (deploy.sh), enum→argv wrapper, offline/online (cloudflared), restart (systemd-run), auto-update pause | [05](05-site-controls.md) §2 | L |
| 4.6 | Dev/Windows graceful degradation for all host controls | [05](05-site-controls.md) §3 | S |

**Gate:** toggling cloudflared flips the live/local badge and public reachability; the
professor is never locked out; every mutation audited.

---

## Phase 5 — History, users, ratings UI

| # | Task | Ref | Size |
|---|---|---|---|
| 5.1 | `GET /api/admin/workflows` (filters/pagination), `/{id}` detail, `/users` rollups | [06](06-workflow-history-and-users.md) §5 | M |
| 5.2 | Workflows tab (`DataTable`, day/user/ip/status filters, drill-in) + Users tab | [06](06-workflow-history-and-users.md) §5 | M |
| 5.3 | `RatingControl` `ui/` primitive (+ showcase + `ui.test.jsx`) | [07](07-ratings.md) §4 | S |
| 5.4 | `POST /api/workflow/{id}/rating` (run + per-question); persist to record + ledger + output folder | [07](07-ratings.md) §3 | M |
| 5.5 | Wire ratings into result bubble/`QuestionCard`; admin override column | [07](07-ratings.md) §4 | M |
| 5.6 | Budget/Audit tabs (gauges, burn-down, audit log) | [03](03-admin-dashboard.md) §4 | M |

---

## Cross-cutting: testing & rollout

- After each phase: `run-tests` (backend pytest+ruff, frontend vitest+eslint), and
  `verify-frontend-ui` for dashboard/rating UI. New endpoints get unit tests; new store
  columns get migration tests (remember `registry._load_raw.cache_clear()` / backend
  restart caveats).
- `deploy.sh` changes (admin secrets, sudoers, restart unit) are the riskiest — test on
  a scratch Pi/VM before the classroom box; keep them behind `ADMIN_HOST_CONTROLS=1`.
- Ship Phase 0 immediately regardless of the admin decision — those are live bugs.
- Land the DB schema (Phase 2) **once**, thoughtfully — later phases add columns but the
  ledger shape should be settled early.

---

## Dependency graph (text)

```
Phase 0 (bugs) ─────────────┐
Phase 1 (auth) ──► Phase 4 (flags/controls) ──► Phase 5 (history/ratings UI)
Phase 2 (identity+store) ──► Phase 3 (accounting/budget) ──► Phase 5
                          └► Phase 5 (history)
```

Auth (1) and identity+store (2) are the two independent foundations; 3, 4, 5 build on
them. Do 0 first and in parallel with everything.
