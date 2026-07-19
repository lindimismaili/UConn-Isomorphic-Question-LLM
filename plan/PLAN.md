# Master Plan — Admin Dashboard + Codebase Hardening

**Repo:** UConn Isomorphic Question Generator (FastAPI + React + OpenAI Agents SDK)
**Date:** 2026-07-16
**Scope:** (1) a deep review of the codebase for bugs/issues/UX, and (2) a phased plan to
add a professor-facing **admin dashboard** (auth, feature toggles, site/backend status &
online-offline control, domain/Cloudflare visibility, workflow history by day/time/user/ip,
output ratings, and per-user rate-limiting with a predictable semester budget).

This folder is the deliverable. Start here, then read the numbered docs.

---

## 📑 Documents

| Doc | Covers |
|---|---|
| **PLAN.md** (this) | Executive summary, headline findings, the budget reality, index |
| [01-codebase-review.md](01-codebase-review.md) | Backend bugs/issues/inconsistencies (severity-ranked, `file:line`) |
| [02-ui-ux-review.md](02-ui-ux-review.md) | Frontend UI/UX + a11y + design-system findings |
| [03-admin-dashboard.md](03-admin-dashboard.md) | Dashboard architecture, feature flags, API/UI composition |
| [04-auth-and-security.md](04-auth-and-security.md) | Professor login, secret handling, hardening |
| [05-site-controls.md](05-site-controls.md) | Status, live/local, online/offline, restart, domain/Cloudflare |
| [06-workflow-history-and-users.md](06-workflow-history-and-users.md) | User identity model + queryable run history |
| [07-ratings.md](07-ratings.md) | Good/bad ratings persisted into workflow data |
| [08-rate-limiting-and-budget.md](08-rate-limiting-and-budget.md) | Token accounting, per-user caps, the cost math |
| [09-implementation-roadmap.md](09-implementation-roadmap.md) | Phased, testable rollout with dependencies |

---

## 🎯 The one thing to read first: the budget reality

The professor's target — **500 users × ~5 generations/day, capped at $2,000 for the
semester** — **does not fit, by roughly 50–100×.** At that volume the semester spend is
realistically **$75k–$190k**, not $2k. No prompt tuning closes a ~90× gap; the *volume*
has to change.

**What actually fits in $2,000** (mid-tier pricing, ~$0.30–0.50 per generation):

- ~**2,600–4,000 total generations for the whole semester**, i.e.
- ~**5–10 generations per student for the entire semester** (not per day), **or**
- a **global cap of ~25–40 generations/day** across everyone, **plus**
- a **hard semester dollar kill-switch** that disables generation at $2,000.

Full sensitivity tables, the per-generation token model, and the recommended default
policy are in [08-rate-limiting-and-budget.md](08-rate-limiting-and-budget.md). **Three
numbers the professor must confirm** drive everything: (1) is $2,000 per-semester or
per-month, (2) the real per-model price, (3) semester length/start.

---

## 🚨 Headline findings from the review

Ranked by what matters most for this project.

1. **No authentication anywhere.** Every `/api/*` route is open — anyone reachable can
   start token-burning workflows, batch over all files, or download any run by UUID. This
   is the root blocker for the *entire* admin ask. → [01](01-codebase-review.md) C-2,
   [04](04-auth-and-security.md).
2. **The live `OPENAI_API_KEY` is committed to git** (in tracked `site/.env`). **Rotate
   it and move it out of the tracked file** — highest-severity single item. →
   [04](04-auth-and-security.md) S1.
3. **No token/usage is captured at all.** `run_llm_stage` discards the SDK `RunResult`
   ([engine.py:210-213](../site/backend/src/workflow/engine.py#L210-L213)). Budget and
   history depend on adding this. → [08](08-rate-limiting-and-budget.md) §4.1.
4. **The store can't answer "by day/user/ip."** It's an opaque `records(kind,id,payload)`
   blob with **timezone-corrupted** timestamps, and **no identity is ever captured**.
   Both must be fixed for the history feature. → [01](01-codebase-review.md) H-2,
   [06](06-workflow-history-and-users.md).
5. **Mode gating (User/Teacher/Dev) is cosmetic** — a `localStorage` string with no
   enforcement. The admin surface must be gated **server-side**, not by `uiMode`. →
   [02](02-ui-ux-review.md) H3.
6. **Real stability bugs:** unbounded upload reads (OOM/DoS), cross-thread dict races in
   the prune loop, and blocking SQLite writes on the 150 ms poll path. →
   [01](01-codebase-review.md) C-1, H-1, H-3.
7. **Frontend robustness gaps:** no error boundary (one bad payload blanks the app), and
   the red **Stop** button silently wipes the whole transcript/logs. →
   [02](02-ui-ux-review.md) H1, H2.

Each doc also lists what's **solid and should not be touched** (traversal guards, the
streaming upload cap, interval cleanup, modal focus-trap, the accessible Dropdown).

---

## 🧭 How the admin features map to reality

| Ask | Feasibility | Notes |
|---|---|---|
| Professor login (user/pass in env) | ✅ straightforward | Password must **not** go in the git-tracked `.env`; use systemd env. [04](04-auth-and-security.md) |
| Enable/disable features | ✅ | Runtime feature-flag table, live-editable. [03](03-admin-dashboard.md) §3 |
| Site/backend status, live vs local | ✅ safe | `systemctl is-active` + `.deploy.env`; read-only. [05](05-site-controls.md) |
| Take online/offline | ⚠️ careful | "Offline" = stop the Cloudflare tunnel (keeps LAN + session). A soft *maintenance flag* is the safer default. [05](05-site-controls.md) |
| Restart backend | ⚠️ delegate | Can't restart own uvicorn in-request; use `systemd-run`. Needs a narrow sudoers allowlist. [05](05-site-controls.md) |
| Domain + Cloudflare info | ✅ safe | Read `.deploy.env` + cloudflared `.yml`; redact the credential. [05](05-site-controls.md) |
| Workflows by day/time/user/ip | ✅ after foundations | Needs identity capture + a queryable ledger + the timezone fix. [06](06-workflow-history-and-users.md) |
| Rate outputs good/bad | ✅ | Persist to record + ledger + output folder; feeds the existing grader loop. [07](07-ratings.md) |
| Per-user request/token limits, budget | ✅ after usage capture | Token-based caps + global semester kill-switch. [08](08-rate-limiting-and-budget.md) |

**Deployment context that shapes all of it:** the app runs on a **Raspberry Pi** via
**systemd + nginx + a Cloudflare tunnel** (units `uconn-isomorphic`, `nginx`,
`uconn-isomorphic-cloudflared`), backend as `www-data` on `127.0.0.1:3002`. Host controls
must respect that privilege boundary and degrade gracefully on the Windows dev box.

---

## 🗺️ Rollout in one screen

- **Phase 0 — Fix the bugs.** Rotate the key, bound uploads, fix timezones + dict races +
  poll-path writes, add the frontend error boundary, fix Stop. Ship regardless of the
  admin decision. *(These are live defects.)*
- **Phase 1 — Auth.** Session login for the professor; `api/admin.py` + `require_admin`;
  audit log.
- **Phase 2 — Identity + queryable store.** Capture user/ip; add the `run_ledger` table
  (settle the schema once).
- **Phase 3 — Accounting + budget.** Capture tokens/cost; per-user caps + semester
  kill-switch.
- **Phase 4 — Feature flags + site controls.** Maintenance mode, status, live/local,
  online/offline, restart, domain/Cloudflare panel.
- **Phase 5 — History, users, ratings UI.** The dashboard tabs on top of the foundations.

Foundations first: **auth (1)** and **identity+store (2)** are the two pillars; 3/4/5 are
mostly presentation over them. Full task table + dependency graph in
[09-implementation-roadmap.md](09-implementation-roadmap.md).

---

## ❓ Decisions needed from you / the professor

1. **Budget:** is **$2,000** per-semester or per-month? Real per-model price? Semester
   length + start date? *(Drives every cap.)*
2. **Fairness model:** per-student quota (everyone gets a little) vs global first-come cap
   vs both? *(Recommend both + the dollar kill-switch.)*
3. **Identity:** anonymous cookie, self-declared name/email (**recommended v1**), or full
   SSO/roster? *(Determines how trustworthy the history/quotas are.)*
4. **Online/offline semantics:** soft maintenance banner (safe) vs hard tunnel stop
   (advanced) — offer both?
5. **Host controls:** enable the sudoers-backed restart/toggle at all, or keep the
   dashboard read-only + maintenance-flag only for v1?

Answering #1 and #3 unblocks the most work.
