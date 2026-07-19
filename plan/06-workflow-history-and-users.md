# 06 — Workflow History & User Identity (query by day / time / user / IP)

> The professor wants to view completed workflows **organized by day / time / user /
> ip**, since students *and* teachers will use the site. This requires two things the
> repo doesn't have yet: **a way to know who ran a workflow**, and **a queryable store**.

---

## 1. The problem today

- **No identity is ever captured.** No route accepts a `Request`, so `request.client.host`
  (IP) and any user identity are never observed or stored
  ([workflows.py:73](../site/backend/src/api/workflows.py#L73),
  [state.py:810-827](../site/backend/src/api/state.py#L810-L827)).
- **The store isn't queryable by these dimensions.** It's a single opaque blob table
  `records(kind, id, payload TEXT, updated_at)`
  ([store.py:29-37](../site/backend/src/workflow/store.py#L29-L37)). The only indexed
  columns are `(kind, id)` and `updated_at`, and `updated_at` is **timezone-corrupted**
  (backend review H-2), so even time math is unreliable today.
- **Run-folder dates are local, record times are UTC.** `create_unique_run_dir` labels
  folders `mm-dd-yyyy` in **local** time ([safety.py:75-108](../site/backend/src/safety.py#L75-L108)),
  while records use UTC — they disagree across the midnight/UTC boundary. Reconcile
  before building day buckets.

---

## 2. User identity model — pick one

Students and teachers must be distinguishable. Options, cheapest → richest:

### A. IP + anonymous session cookie (no login) — **fastest, weakest**
Assign each browser a random `user_id` cookie on first visit; also record IP. Good
enough to *group* runs and rate-limit, but a user is just "browser X from IP Y" — no
name, resets if cookies clear, shared/NAT IPs blur students together on campus wifi.

### B. Lightweight name/email gate (self-declared) — **recommended v1**
Before generating, the user enters a name or email (optionally a class/section). Stored
as `user_id` (hashed email) + `display_name`. No password, low friction, gives the
professor human-readable history and per-student quotas. Spoofable, but adequate for a
class tool and dramatically better than anonymous.

### C. Roster / SSO (UConn NetID) — **strongest, most work**
Real authentication (SSO/OAuth or a professor-managed roster). Trustworthy identity and
per-student accountability, but a much larger build and an institutional-integration
dependency. Defer unless the professor needs graded accountability.

**Recommendation:** ship **B** now (name/email gate), structured so `user_id` is the
stable key everywhere (history, ratings, budget). Leave a clean seam to swap in **C**
later without touching the ledger schema.

### Privacy
IPs are PII. Restrict all of it behind `require_admin`, document retention (e.g. purge
raw IP after N days, keep an HMAC-truncated form for abuse-grouping), and state it in a
short privacy note for students. Coordinate with the auto-prune TTLs.

---

## 3. Capture identity (backend E-1)

Add `request: Request` to the start handlers (`start_workflow`, `rerun-from-agent`,
`start-from-agent-input`, `create-data/start`, `eval-batch/start`) and:

- Read `request.client.host`. **Behind nginx/cloudflared the real client IP is in
  `X-Forwarded-For` / `CF-Connecting-IP`** — trust a forwarded header **only** from the
  known proxy (nginx already sets `X-Real-IP`/`X-Forwarded-For`, deploy.sh nginx block).
  Take the first hop, validate it's an IP.
- Read `user_id`/`display_name` from the session/identity cookie (model B).
- Thread both into `_new_workflow_record` and into the new ledger row at creation.

---

## 4. Queryable store (backend E-4)

Rather than overload the blob table, add a purpose-built **append-only ledger** (shared
with budget — see [08](08-rate-limiting-and-budget.md) §4.2). One row per run, written at
start and reconciled at completion:

```sql
CREATE TABLE IF NOT EXISTS run_ledger (
  workflow_id   TEXT PRIMARY KEY,
  kind          TEXT,               -- 'workflow' | 'create_data' | 'eval_batch'
  user_id       TEXT,
  display_name  TEXT,
  client_ip     TEXT,               -- consider HMAC/truncate at rest
  created_at    TEXT,               -- aware UTC ISO
  finished_at   TEXT,
  day_utc       TEXT,               -- YYYY-MM-DD (document UTC vs local; pick one)
  status        TEXT,               -- completed|errored|cancelled|blocked|lost
  ran_evaluation INTEGER,
  input_tokens  INTEGER,
  output_tokens INTEGER,
  total_tokens  INTEGER,
  est_cost_usd  REAL,
  rating        INTEGER,            -- -1 bad / +1 good / NULL (see 07)
  output_folder TEXT
);
CREATE INDEX IF NOT EXISTS idx_run_day  ON run_ledger(day_utc);
CREATE INDEX IF NOT EXISTS idx_run_user ON run_ledger(user_id, day_utc);
CREATE INDEX IF NOT EXISTS idx_run_ip   ON run_ledger(client_ip);
```

- Write from `save()`/the engine finalize seam by lifting fields out of the payload; keep
  the existing blob `records` table as the full-detail source of truth (the ledger is the
  index over it).
- **Fix H-2 at the source:** store aware-UTC timestamps here so every downstream query is
  correct.
- Add query methods to `WorkflowStore`: `by_day(day)`, `by_user(user_id)`, `by_ip(ip)`,
  `range(start, end)`, plus aggregates (`count`, `sum(total_tokens)`, `sum(cost)`)
  grouped by day/user. `list_records` today only does `ORDER BY updated_at` for one kind
  ([store.py:75-89](../site/backend/src/workflow/store.py#L75-L89)) — insufficient.

---

## 5. Admin API + UI

- `GET /api/admin/workflows?day=&user=&ip=&status=&from=&to=&page=` → paginated ledger
  rows.
- `GET /api/admin/workflows/{id}` → full detail (joins the blob record + result summary
  via the existing `_run_summary`).
- `GET /api/admin/users` → per-user rollups (run count, tokens, cost, last-seen).
- **UI:** a **Workflows** tab (`DataTable`) with column sort + filters (day picker, user,
  ip, status), each row drilling into a detail drawer (reuse `FinalResultModal`/eval
  viewers). A **Users** tab with per-student rollups and their quota status. Group-by-day
  is the default view (matches the ask).

---

## 6. Acceptance criteria
- Every new run creates a ledger row with user_id, ip, aware-UTC created_at, day bucket.
- Admin can filter/sort completed runs by day, time, user, and ip, and open any run's
  detail.
- Timestamps are correct across timezones (H-2 fixed); folder-date vs record-date
  reconciled.
- IP/PII is admin-only, with a documented retention policy.

*Cross-refs: identity feeds ratings ([07](07-ratings.md)) and budget ([08](08-rate-limiting-and-budget.md));
ledger schema is shared with [08](08-rate-limiting-and-budget.md).*
