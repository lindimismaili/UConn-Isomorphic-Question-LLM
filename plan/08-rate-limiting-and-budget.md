# 08 — Rate Limiting & Token Budget

> **Status — planning model, not an authoritative price quote or implemented
> budget ledger; status warning added July 27, 2026.** Recheck current model pricing
> before decisions. The implemented limiter is an in-process request throttle,
> not the per-user/semester accounting proposed below.

> **Purpose:** answer the professor's core question — *"if 500 people each do ~5
> generations a day, how much is that in tokens, and can we cap the semester at
> $2,000?"* — and specify the throttling system that keeps spend predictable.

---

## 0. TL;DR (read this first)

- **The repo captures ZERO token usage today.** `run_llm_stage()`
  ([engine.py:210-213](../site/backend/src/workflow/engine.py#L210-L213)) returns
  only `result.final_output` and discards the SDK `RunResult`, which is where
  `context_wrapper.usage` (input/output/total tokens) lives. **Step one of this
  whole feature is capturing usage.** Everything else depends on it.
- **500 users × 5 generations/day does NOT fit in $2,000 for a semester** under
  this document's historical $0.30–$0.75/run assumptions: the sensitivity
  table gives **$75,000–$187,500** over 100 days.
- At those same assumptions, **$2,000** buys about **2,667–6,667 total
  generations for the whole semester** — i.e. **~5–13 generations per student
  for the semester**, or a **global cap of ~26–66 generations/day**. Not 5
  per student per day.
- The right primitive is **token-based accounting with a hard global kill-switch**,
  not a raw request counter — because cost is a function of tokens, and one heavy
  PDF + evaluation run can cost 10× a light one.

The numbers below show the sensitivity so the professor can pick a policy with
eyes open.

---

## 1. What one "generation" actually costs

A normal generation with evaluation disabled uses five core model stages,
two model-backed guardrail classifiers, two model-backed container shell tasks
(bootstrap and compile), moderation, and any tool/retry turns. Evaluation is
separate and opt-in: it runs eight rubric graders for every evaluated item
rather than one ordinary pipeline call. The registry's ten-node taxonomy is
therefore not a billing count. Several calls use reasoning effort, and
reasoning tokens may be billed as output tokens.

We do not yet have measured numbers (that's the point of the usage-capture work),
so this uses **stated assumptions**. Treat them as a model to replace with real
telemetry once capture ships.

### Per-generation token model (order-of-magnitude)

| Stage | Input tok | Output tok (incl. reasoning) | Notes |
|---|---:|---:|---|
| input guardrail | 1k | 0.3k | small classifier |
| normalizer | 1k | 0.5k | parse the instruction |
| extracter | 8k | 3k | reads the PDF; large PDFs balloon input |
| content guardrail | 3k | 0.5k | on extracter output |
| verifier | 6k | 5k | reasoning; fills explanations |
| generator | 9k | 8k | reasoning `medium`; writes questions |
| latexifier | 10k | 5k | reasoning + code-interpreter tool calls |
| **evaluation (opt-in)** | 25k | 18k | 8 criteria, some `high` reasoning |
| **Subtotal, no eval** | ~38k | ~22k | **≈ 60k tokens** |
| **Subtotal, with eval** | ~63k | ~40k | **≈ 103k tokens** |

Heavy real-world runs (big multi-question PDFs, retries, latexifier recompiles) can
be **2–4× larger**. So a defensible planning range per generation:

- **Light run, no eval:** ~50k tokens
- **Typical run, no eval:** ~80k tokens
- **Typical run + eval:** ~120k tokens
- **Heavy run + eval + retries:** ~350k tokens

> **Mitigating fact already in the code:** `runEvaluation` defaults to **false** in
> [`start_workflow`](../site/backend/src/api/workflows.py#L79) (`_parse_form_bool(runEvaluation, default=False)`).
> Evaluation is the single most expensive stage, so the *default* generation is the
> cheaper "no-eval" path. Keep it that way for students; gate eval behind Teacher/Dev.

### Per-generation dollar cost

Pricing depends on the model. The pipeline now runs a **two-tier policy** (set in
`agents.json`): the **Generator** uses **`gpt-5.6-terra` at high reasoning** — the cost
driver, because high reasoning multiplies billed output tokens — and **every other LLM
stage plus the grader** use the cheaper **`gpt-5.6-luna`**. So generation dominates spend
and the extract/verify/evaluate stages are comparatively cheap. **Confirm the real
per-model price for `terra` and `luna` with the provider before committing** — this is the
single biggest lever on the answer, and the terra/high-reasoning output price matters most.

Assumed blended price bands (input / output per 1M tokens):

| Scenario | $/1M in | $/1M out |
|---|---:|---:|
| Cheap tier | $0.50 | $4 |
| Mid tier (planning default) | $1.25 | $10 |
| Premium tier | $2.50 | $15 |

Cost of a **typical no-eval run** (~38k in / 22k out):

| Tier | Cost |
|---|---:|
| Cheap | $0.11 |
| **Mid (default)** | **$0.27** |
| Premium | $0.42 |

Cost of a **typical run + eval** (~63k in / 40k out):

| Tier | Cost |
|---|---:|
| Cheap | $0.19 |
| **Mid (default)** | **$0.48** |
| Premium | $0.76 |

**Planning point estimates:** ~**$0.30/generation** without eval, ~**$0.50/generation**
with eval, mid tier.

---

## 2. The 500-users × 5/day scenario

- 500 users × 5 gens/day = **2,500 generations/day**.
- Semester length: **confirmed ~5 months** → use **~100 teaching weekdays** (or ~150
  calendar days if usage is daily including weekends — which makes the overage below even
  larger).
- **250,000 generations** over the semester.

| Per-gen cost | Daily spend | Semester spend (100 days) |
|---|---:|---:|
| $0.30 (no eval) | $750/day | **$75,000** |
| $0.50 (with eval) | $1,250/day | **$125,000** |
| $0.75 (heavier) | $1,875/day | **$187,500** |

**Conclusion: at 5 gens/user/day, a $2,000 semester budget is not achievable by
~40–90×.** No amount of prompt tuning closes a 90× gap. The policy has to change the
*volume*, not just the per-unit cost.

### Token totals (for the "how many tokens?" question)

At ~80k tokens/gen (no eval): 250,000 × 80k = **20 billion tokens/semester**
(~200M tokens/day). With eval (~120k): **30 billion tokens/semester**. These are the
raw magnitudes; the dollar figure is what actually binds.

---

## 3. What actually fits in $2,000

Invert the math — how many generations does $2,000 buy?

| Per-gen cost | Generations in $2,000 | Per user (500 users), whole semester | Global cap/day (100 days) |
|---|---:|---:|---:|
| $0.30 | 6,667 | ~13 | ~66/day |
| $0.50 | 4,000 | ~8 | ~40/day |
| $0.75 | 2,667 | ~5 | ~26/day |

**So a realistic, safe policy for a $2,000 semester is one of:**

- **Per-user semester quota: ~8–12 generations total per student** (not per day), OR
- **Per-user daily quota of 1–2**, combined with a **hard global daily ceiling of
  ~30–50 generations**, whichever trips first, **and a hard semester dollar cap that
  disables generation entirely when hit.**

This is a program-design decision for the professor. Section 5 recommends defaults
and makes them all env-configurable so they can be tuned once real telemetry exists.

---

## 4. The accounting & throttling design

### 4.1 Capture usage (foundational — do this first)

In [engine.py](../site/backend/src/workflow/engine.py), change `run_llm_stage()` to
return usage alongside output, or accumulate into the workflow record:

- Non-streaming path: `result.context_wrapper.usage` → `input_tokens`,
  `output_tokens`, `total_tokens`.
- Streaming path: `streamed.context_wrapper.usage` after the stream drains.
- Accumulate a `usage` dict on the workflow record:
  `{ "by_stage": {stage: {in, out, total}}, "total_tokens": N,
     "est_cost_usd": C }` where `C` is computed from an env-configured price table
  (`PRICE_INPUT_PER_M`, `PRICE_OUTPUT_PER_M`, or a per-model map).
- The evaluation stage also spends tokens via `client.evals`; capture what the Evals
  API returns (or estimate from item count × criteria) and add it in.

This is a low-risk, additive change — no behavior changes, just record-keeping.

### 4.2 A usage ledger (new SQLite table)

The current store is a single `records(kind, id, payload)` blob table
([store.py:29-37](../site/backend/src/workflow/store.py#L29-L37)) — not queryable by
user/day/tokens. Add a dedicated append-only table (see
[06-workflow-history-and-users.md](06-workflow-history-and-users.md) for the shared
schema). Minimum columns:

```
usage_ledger(
  workflow_id TEXT, user_id TEXT, ip TEXT, created_at TEXT (ISO, UTC),
  day TEXT (YYYY-MM-DD, user-local or UTC — pick one and document it),
  ran_evaluation INTEGER, input_tokens INT, output_tokens INT,
  total_tokens INT, est_cost_usd REAL, status TEXT
)
```

Index on `(user_id, day)` and `(day)`. This one table powers per-user limits, the
global daily/semester counters, and the admin history views.

### 4.3 Enforcement points (a pre-flight gate)

Add a `check_and_reserve_budget(user_id, ip)` call at the top of every generation
entrypoint — `start_workflow`, `rerun-from-agent`, `start-from-agent-input`, and the
batch/create-data starts. It runs **before** `get_openai_client()` and returns HTTP
`429` with a structured body when a limit trips:

1. **Per-user daily cap** — count today's ledger rows / tokens for `user_id`; reject
   if `>= USER_DAILY_LIMIT`.
2. **Per-user semester cap** (optional) — same over the semester window.
3. **Global daily cap** — sum of all users today; reject if `>= GLOBAL_DAILY_LIMIT`.
4. **Global semester dollar cap (kill-switch)** — sum `est_cost_usd` since the
   semester start; if `>= SEMESTER_BUDGET_USD`, disable generation for everyone and
   surface a banner. This is the hard stop that guarantees the $2,000 ceiling.

Because token cost is only known *after* a run, do **reservation + reconciliation**:
reserve a pessimistic estimate (e.g. 120k tokens) at the gate, then write the real
usage to the ledger when the run finishes and release the difference. This prevents a
burst of concurrent starts from blowing past the cap.

### 4.4 Limit by tokens, not just request count

Request counts are simple but a heavy PDF costs 10× a light one. Recommended: track
**both**, cap primarily on **tokens/cost**, and show the request count to users as the
friendly number. Example env-driven policy:

- `USER_DAILY_TOKEN_LIMIT` (e.g. 300k → ~3–4 typical gens)
- `USER_DAILY_REQUEST_LIMIT` (e.g. 5, as a coarse backstop)
- `GLOBAL_DAILY_COST_LIMIT_USD` (e.g. $25)
- `SEMESTER_BUDGET_USD` (e.g. 2000) + `SEMESTER_START_DATE`

All overridable from `.env` and (Phase 2) editable live from the admin dashboard's
feature-flag store.

### 4.5 User feedback

- `429` response body: `{ reason, limit, used, resets_at }` so the frontend shows
  *"You've used your 3 generations for today — resets at midnight."*
- Admin dashboard: live gauges for global daily spend and semester budget burn-down,
  with the kill-switch state.

---

## 5. Recommended default policy (starting point)

Encoded as env defaults; tune once telemetry is real. Assumes mid-tier pricing and a
~$0.30–0.50 typical generation.

| Knob | Default | Rationale |
|---|---|---|
| `SEMESTER_BUDGET_USD` | 2000 | hard ceiling; kill-switch at 100%, warn at 80% |
| `GLOBAL_DAILY_COST_LIMIT_USD` | 25 | ~$25 × ~80 days ≈ $2,000 with headroom |
| `USER_DAILY_REQUEST_LIMIT` | 3 | friendly cap; most students need a few tries |
| `USER_DAILY_TOKEN_LIMIT` | 300000 | catches heavy PDFs before they dominate |
| `EVAL_REQUIRES_ROLE` | teacher | keep the pricey grader off the student path |
| default `runEvaluation` | false | already the code default — keep it |

**Cost-reduction levers to combine with the caps:**

- Keep evaluation opt-in / teacher-only (biggest single saver).
- Enable `ENABLE_STAGE_CACHE` and encourage **rerun-from-agent** so students iterate
  without re-paying for extraction/verification.
- The two-tier model split already helps: only the Generator pays for `gpt-5.6-terra` +
  high reasoning; everything else is `gpt-5.6-luna`. If cost is still too high, drop the
  Generator to `luna` or lower its reasoning for the student-facing default and reserve
  `terra`/high for teacher runs (per-node model + reasoning are data-driven in
  `agents.json`).
- Cap `maxTurns` tightly (already configurable per agent) to bound retry blow-ups.

---

## 6. Open questions for the professor

1. Is **$2,000** the whole-semester ceiling, or per-month? (Changes caps ~4×.)
2. Real **per-model pricing** for the deployed model — this dominates every number.
3. Semester **length and start date**, and whether weekends count.
4. Is the goal *"everyone gets a little"* (per-user quota) or *"first-come until the
   pot runs out"* (global cap)? Recommend **both**, with the global dollar cap as the
   non-negotiable backstop.
5. Should hitting the cap **block** or **queue/degrade** (e.g. fall back to a cheaper
   model)? Recommend block with a clear message for v1.

---

*Cross-refs: usage capture → [09-implementation-roadmap.md](09-implementation-roadmap.md)
Phase 3; ledger schema shared with
[06-workflow-history-and-users.md](06-workflow-history-and-users.md); user identity
(needed to attribute usage) → [04-auth-and-security.md](04-auth-and-security.md).*
