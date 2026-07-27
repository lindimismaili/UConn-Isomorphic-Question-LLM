# 01 — Backend Codebase Review (bugs, issues, inconsistencies)

> **Status — dated audit; status warning added July 27, 2026.** Findings describe
> evidence at review time, not guaranteed current behavior. Reverify each item
> against the code and tests before acting; current handoff status lives in
> [`docs/PROFESSOR_HANDOFF.md`](../docs/PROFESSOR_HANDOFF.md).

Findings from a deep read of `site/backend/src/` and `site/backend/data/`, grouped by
severity. Each item has a `file:line` anchor, the problem, and a fix. Items marked
**[blocks admin]** are prerequisites for the admin-dashboard work.

---

## Critical

### C-1 — Unbounded file reads into memory on upload routes (DoS/OOM) **[blocks admin]**
[workflows.py:232](../site/backend/src/api/workflows.py#L232) (`await agentInput.read()`),
[eval_batch.py](../site/backend/src/api/eval_batch.py) `:90/:100` read the **entire**
uploaded body into RAM *before* any size check. Any client can POST a multi-GB body to
`/api/workflow/start-from-agent-input` or `/api/eval-batch/start` and exhaust memory.
Note `/api/upload` ([files.py:88-113](../site/backend/src/api/files.py#L88-L113))
does it **correctly** — streaming chunks with a running cap.
**Fix:** reuse the streaming/bounded-read pattern from `files.py`; abort once the cap
is exceeded, or add a global body-size ASGI limit.

### C-2 — No authentication or per-client throttling on any state-changing route **[blocks admin]**
Every endpoint is open. Anyone reachable can start workflows (each spins up an OpenAI
container and burns tokens — [engine.py:843-848](../site/backend/src/workflow/engine.py#L843-L848)),
batch over all bundled eval files, upload, or download any run's artifacts by knowing
its UUID. No rate limit, no accounting.
**Fix:** this is the root of the whole request — see
[04-auth-and-security.md](04-auth-and-security.md) and
[08-rate-limiting-and-budget.md](08-rate-limiting-and-budget.md). Add an identity layer
and a limiter *before* the accounting/rating features.

---

## High

### H-1 — Cross-thread mutation of shared dicts → `dictionary changed size during iteration`
The pruning loop runs `_prune_*` inside `asyncio.to_thread`
([state.py:674-676](../site/backend/src/api/state.py#L674-L676)) — on a worker thread —
while the event loop mutates the same module-level `workflows`/`batch_jobs` dicts
(e.g. [state.py:342](../site/backend/src/api/state.py#L342),
[state.py:744](../site/backend/src/api/state.py#L744),
[state.py:1430](../site/backend/src/api/state.py#L1430)). The GIL doesn't prevent a
mid-scan size-change error; prune crashes are silently swallowed at
[state.py:677](../site/backend/src/api/state.py#L677).
**Fix:** run pruning on the loop over a snapshot taken **on the loop thread**, or guard
all three dicts with a `threading.Lock`. (Snapshotting inside the thread still races.)

### H-2 — Timezone corruption in every TTL/stale/age computation **[blocks admin]**
`updated_at` is written UTC-with-`Z` ([safety.py:23-25](../site/backend/src/safety.py#L23-L25))
but parsed as `fromisoformat(raw.replace("Z",""))` → **naive** datetime, then
`.timestamp()` treats it as **local**, compared against a UTC now
([state.py:340-347](../site/backend/src/api/state.py#L340-L347), `:389-398`, `:429-437`).
Every TTL, "lost"-detection, and batch-expiry fires off by the host's UTC offset. This
also **breaks the requested "query workflows by day/time" feature** at the root.
**Fix:** parse aware UTC (`raw.replace("Z","+00:00")`) or store an epoch column; unify
the mixed `datetime.now().isoformat()` (naive local, e.g. `state.py:833,862`) vs
`utc_now_iso()` timestamps.

### H-3 — Blocking SQLite writes on the ~150 ms polling hot path
`GET /api/workflow/{id}/status` calls `_touch_workflow`
([workflows.py:53](../site/backend/src/api/workflows.py#L53)) → synchronous SQLite
`INSERT…ON CONFLICT` **on the event loop** ([state.py:280-283](../site/backend/src/api/state.py#L280-L283)
→ [store.py:39-52](../site/backend/src/workflow/store.py#L39-L52)). Each active workflow
drives ~7 WAL writes/sec on the loop, blocking other requests. Worse, **touching on a
read** resets `updated_at`, so a polled workflow never becomes stale/TTL-eligible.
Same pattern in `batch.py:146-147`, `eval_batch.py:124-135`.
**Fix:** status GET must be pure-read; if a heartbeat is needed, debounce and route
through `to_thread`.

### H-4 — Eval-batch records are never persisted (silent durability loss)
`start_eval_batch` never sets `batch["id"]` nor calls `_persist_batch`
([eval_batch.py:112](../site/backend/src/api/eval_batch.py#L112)). The only save path,
`_touch_eval_batch` → `_batch_id_for_record`, scans only `batch_jobs` — **not**
`eval_batch_jobs` ([state.py:269-278](../site/backend/src/api/state.py#L269-L278)) — so
it returns `""` and `_persist_batch("",…)` no-ops. Eval batches live only in memory and
vanish on restart/prune.
**Fix:** set `batch["id"]=batch_id` and persist at creation; make `_batch_id_for_record`
also scan `eval_batch_jobs`.

### H-5 — Create-data and eval batches share the store `kind="batch"` namespace
Both persist under `kind="batch"` and both load `kind="batch"`
([state.py:301-310](../site/backend/src/api/state.py#L301-L310), `:456-464`). The record
shapes differ; after a restart a lookup can return the wrong blob and be summarized with
the wrong serializer (`_summarize_batch_entries` vs `_summarize_eval_batch_entries`).
**Fix:** distinct kinds (`"batch"` vs `"eval_batch"`) or a discriminator validated on
load.

---

## Medium

- **M-1 — No single-workflow cancel endpoint.** Batches have `/stop`; a single run does
  not. `_mark_workflow_cancelled` + `workflow_tasks` already exist — wire
  `POST /api/workflow/{id}/stop` → `workflow_tasks[id].cancel()`. (Also needed so the
  admin dashboard can kill a runaway/expensive run.)
- **M-2 — SSE stream has no disconnect detection or max lifetime**
  ([workflows.py:302-340](../site/backend/src/api/workflows.py#L302-L340)). A walked-away
  client spins a 100 ms coroutine forever. Add `await request.is_disconnected()` + an
  absolute timeout.
- **M-3 — `POST /api/outputs/{label}/compare-professor` is a cost/state-changing
  operation.** It reads the shared professor workbook but writes the Agreement
  sheet and reports into the selected batch's `XLSX/compiled.xlsx`
  ([professor_comparison.py](../site/backend/src/tools/professor_comparison.py)).
  Keep it behind the access boundary and audit its per-batch writes.
- **M-4 — Blocking filesystem writes inside async batch coroutines**
  (`state.py:1474-1491`, and `run_eval_batch_async` `write_json` at `:566,599,602,616,624,634`).
  Route through `to_thread` like the engine does.
- **M-5 — Shallow upload content-type validation.** Magic bytes checked only for `.pdf`
  ([state.py:110-112](../site/backend/src/api/state.py#L110-L112)); `.svg/.docx/.tex`
  accepted on extension alone and fed to the code-interpreter container. The
  eval-batch path does no suffix allow-listing; `safe_filename` preserves the
  supplied suffix because `allowed_suffixes` is not passed. Tighten both.
- **M-6 — `download_file` authorization is by-obscurity only**
  ([files.py:193-230](../site/backend/src/api/files.py#L193-L230)) — traversal is
  correctly blocked, but knowing the UUID = access. Acceptable only behind auth (C-2).

---

## Low / consistency

- **L-1** — CORS `allow_methods/headers=["*"]` ([server.py:64-66](../site/backend/src/server.py#L64-L66)); broader than needed (credentials off, so limited impact). A prod with a different frontend origin and unset `ALLOWED_ORIGINS` silently breaks.
- **L-2** — `openai/status` uses blocking `urllib.urlopen` in an async handler ([health.py:41-77](../site/backend/src/api/health.py#L41-L77)); 5s can block the loop. Use `to_thread`/async client.
- **L-3** — Upload silently drops parts with empty filenames ([files.py:63-64](../site/backend/src/api/files.py#L63-L64)).
- **L-4** — `_load_upload_manifest` swallows malformed JSON as "no files" ([state.py:1085-1094](../site/backend/src/api/state.py#L1085-L1094)); add a warning log.
- **L-5** — `WorkflowStore` opens a fresh connection per op ([store.py:19-23](../site/backend/src/workflow/store.py#L19-L23)), re-running PRAGMAs each time; compounds H-3. Use one long-lived guarded connection or a small pool.
- **L-6** — Naming drift: `EVALUATION_FIXTURES_DIR` re-aliased to `EVALUATION_DIR` in three places; `_prune_eval_batch_jobs`/`_summarize_eval_batch_entries` use terse one-liner style inconsistent with their siblings.

---

## Enablement notes for the admin features (from the engine seams)

- **Identity/IP (E-1):** no route accepts `Request`, so IP/user is never observed. Add
  `request: Request` to the start handlers and thread `user`/`ip` into
  `_new_workflow_record` ([state.py:810-827](../site/backend/src/api/state.py#L810-L827)).
- **Token/cost capture (E-2):** the capture point is `run_llm_stage`
  ([engine.py:191-235](../site/backend/src/workflow/engine.py#L191-L235)) — it discards
  the `RunResult` whose `context_wrapper.usage` holds token counts. See
  [08-rate-limiting-and-budget.md](08-rate-limiting-and-budget.md) §4.1.
- **Ratings (E-3):** no field/route/schema slot exists. See
  [07-ratings.md](07-ratings.md).
- **Queryable history (E-4):** the store is an opaque `records(kind,id,payload)` blob;
  add real columns + indexes. Schema in
  [06-workflow-history-and-users.md](06-workflow-history-and-users.md). Reconcile the
  *local* `mm-dd-yyyy` run-folder labels ([safety.py:75-108](../site/backend/src/safety.py#L75-L108))
  with UTC record times before building day buckets.

---

## Recommended fix order (backend)
1. **C-1 + C-2** — bounded uploads + auth/trust boundary.
2. **H-2 + the E-4 schema** — do together; same code, foundation for accounting/history.
3. **H-1 + H-3** — dict races + blocking-writes-on-poll (stability under load).
4. **H-4 + H-5** — batch durability + kind collision.
5. Then M-1…M-6 and the accounting/rating/limit build-out.

*What's solid (don't "fix"): the traversal guards in `_resolve_output_folder_path`,
`_source_paths_from_session`, and `download_file`; the streaming upload cap in
`files.py`; the zip temp-file rename; the lazy OpenAI client.*
