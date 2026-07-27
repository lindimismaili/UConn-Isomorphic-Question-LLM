# UConn Isomorphic Question Generator — AGENTS.md

Onboarding guide for coding agents (Claude, Codex, etc.) working in this repo.
**This file is the single source of truth for the repo** — `CLAUDE.md` just points
here. Older `.claude/CLAUDE.md` / `.codex/AGENTS.md` references are stale.

## Repository Docs

Primary maintained documents:

- [README.md](README.md) — human-facing setup, environment, run/CLI/output reference
- [CLAUDE.md](CLAUDE.md) — Claude Code entry point; a pointer straight to this file
- [deploy/PROFESSOR_HANDOFF.md](deploy/PROFESSOR_HANDOFF.md) — professor setup, access/cost controls,
  Raspberry Pi operations, backup boundaries, limitations, and release checklist
- [site/SECURITY.md](site/SECURITY.md) — deployment boundary, secrets, abuse limits, privacy, and reporting
- [site/backend/BACKEND.md](site/backend/BACKEND.md) — backend reference: what every file does,
  key functions, the 10-node workflow, the HTTP API routes, `data/` files, and tests
- [site/frontend/DESIGN.md](site/frontend/DESIGN.md) — frontend design system: tokens, layout,
  buttons, modals, themes, transitions, consistency rules (**read before any UI change**)
- [deploy/DEPLOY.md](deploy/DEPLOY.md) — deployment + local preview scripts (operational, not source of truth)
- [deploy/BACKUP_RESTORE.md](deploy/BACKUP_RESTORE.md) — executable Pi backup,
  restore, interrupted-transaction recovery, and rehearsal runbook
- [archive/ARCHIVE.md](archive/ARCHIVE.md) — historical GPT Builder/question-source/report inventory
- [site/backend/data/calibration/README.md](site/backend/data/calibration/README.md) — evaluation
  calibration artifact format and maintenance notes

## What This Repo Is

FastAPI + React app that generates isomorphic physics multiple-choice questions
from uploaded source documents using the OpenAI Agents SDK.

Full runtime pipeline (10 registry nodes):

`setup → input_guardrail → normalizer → extracter → content_guardrail → verifier → generator → latexifier → evaluation → output`

- `site/backend/data/agents.json` is the **single source of truth** for all 10 nodes plus
  global constants (`_config`). Python reads through `site/backend/src/registry.py`;
  the frontend derives from `GET /api/agents` via `site/frontend/src/hooks/useAgentRegistry.js`.
- `setup` and `output` are `kind: "runtime"` nodes; `input_guardrail` and `content_guardrail`
  are `kind: "guardrail"` nodes; the other six are `kind: "agent"` nodes. So the registry
  taxonomy is **6 agent + 2 runtime + 2 guardrail**. This is not a billing count:
  the two guardrail nodes make at least three classifier calls (input
  injection, content injection, and academic/content classification), and the
  setup/compile shell orchestration is also model-backed; only the final output
  node is wholly local.
- Guardrail nodes appear (and animate) in the pipeline visual but are **excluded from the
  runnable/stoppable/LLM sets** — `input_to_agent_sequence()`, `llm_agents()`, and
  `stoppable_agents()` all skip `kind == "guardrail"`.
- Both the FastAPI server and the CLI call the same workflow engine.

## Source-Of-Truth Paths

Backend:

- `site/backend/src/server.py` — FastAPI app factory (CORS, lifespan, static mount).
  Launch with `uvicorn backend.src.server:app` or `python -m backend.src.server`.
- `site/backend/src/api/` — route handlers split by domain (`health`, `config`,
  `files`, `workflows`, `batch`, `eval_batch`, `professor_comparison`) plus
  `state.py` (in-memory registries, config, helpers, workflow-runner wrappers,
  background pruning loop) and `upload_budget.py` (process-wide live upload
  storage admission/reservations coordinated with cleanup)
- `site/backend/src/workflow/` — shared engine, agent factory, payload builders,
  resume/replay helpers, SQLite `store.py` (durable recovery mirror),
  and `guardrails.py` (the three moderation/jailbreak/on-topic guardrails)
- `site/backend/src/auth.py` — optional shared-token API boundary,
  `X-App-Access-Token` authentication, cost-route throttling, and PayPal URL validation
- `site/backend/src/api/idempotency.py` — required paid-start
  `X-Idempotency-Key` validation, canonical request fingerprints, durable
  duplicate ownership/replay, and changed-input conflict handling
- `site/backend/src/bootstrap.py` — local environment loading plus an owner-only
  `0077` process umask for new assessment data and artifacts
- `site/backend/src/safety.py` — the fixed 20,000-character instruction
  boundary plus shared filename, contained-path, collision, and JSON helpers
- `site/backend/src/cost_controls.py` — strict question, batch, grader-token,
  ZIP, and optional style-review amplification limits
- `site/backend/src/provider_timeouts.py` — bounded async stage/guardrail,
  container-tool, eval-poll, and sync SDK transport deadlines shared by server
  and CLI entry points
- `site/backend/src/provider_clients.py` — one application-owned Agents SDK
  `AsyncOpenAI` provider with the same finite transport/retry policy, safe
  shutdown, and restart-required key rotation
- `site/backend/src/deployment_maintenance.py` — Pi-only cross-process paid-work
  admission. Web/CLI starts hold a shared activity-lock lease through provider
  and artifact cleanup; deploy/backup/restore take the conflicting exclusive
  lease before maintenance.
- `site/backend/src/main.py` — CLI workflow orchestration (delegates to the engine)
- `site/backend/src/container.py` — owned OpenAI container bootstrap + LaTeX compile
- `site/backend/src/evaluation.py` — OpenAI Evals integration;
  `na_count`/`coverage_mismatch` reporting
- `site/backend/src/pipeline_validation.py` — deterministic generator/verifier
  contract checks, incl. `validate_distractor_logic_preservation`
- `site/backend/src/tools/judge_agreement.py` — judge ↔ professor κ / agreement metrics
- `site/backend/src/tools/meta_evaluator.py` — PROPOSE-ONLY grader self-improvement CLI
  (mines rationales + `FAILURE_TAGS` + deterministic checks → ranked file-targeted proposals;
  reward-hacking guard, optional κ-gate, experiment ledger, versioned calibration format)
- `site/backend/src/registry.py` — **lazy-loaded gateway** for all agent constants
  (`@lru_cache`); import from here instead of hardcoding names anywhere in Python.
  Guardrail accessors live here too: `guardrails_enabled()`, `guardrail_model()`,
  `guardrail_moderation_model()`, `guardrail_physics_min_confidence()`,
  `guardrail_block_off_topic()`, `guardrail_fail_closed()`, `guardrail_max_questions_sampled()`,
  `guardrail_input_node()`, `guardrail_content_node()`, `guardrail_nodes()`
- `site/backend/src/schemas.py`, `site/backend/src/helpers.py`, `site/backend/src/safety.py`,
  `site/backend/src/paths.py`
- `site/backend/data/agents.json` — single source of truth for all 10 nodes; every entry
  carries `sequence`, `name`, `description`, `model`, `reasoning`, `kind`, `isLLM`,
  `hasPrompt`, `acceptsSourceFiles`, `isStoppable`, `useCodeInterpreter`,
  `countLabel`, `aliases`, `inputFrom`/`outputTo` (flow display names), and
  `cliDescription`. Counted nodes add `countField`; runnable/result-bearing
  nodes add `resultField`; normal Agents SDK stages add `maxOutputTokens` and
  may add `maxTurns` (default 6, latexifier 12);
  the evaluation entry adds `promptName`; the setup entry adds `allowedDomains` +
  `containerPythonPackages` + its shell-task `maxOutputTokens`; `_config` holds
  `themes`, `apiTitle`, `apiDescription`, and
  `guardrails` (`enabled`, `model`, `moderationModel`, `reasoning`, `physicsMinConfidence`,
  `blockOffTopic`, `failClosed`, `maxQuestionsSampled`, `maxOutputTokens`,
  `inputNode`, `contentNode`)
- `site/backend/data/prompts/*.txt` — agent prompt instructions (including the guardrail
  classifier prompts `input_guardrail.txt` / `content_guardrail.txt`); `bootstrap.txt` and
  `bootstrapCompile.txt` are container shell-task templates with `{var}` placeholders
- `site/backend/data/rubric.json` — eval rubric criteria only (pass/fail scale, 8 criteria,
  each with an optional per-criterion `reasoning` effort); grader model comes from
  `agents.json` `evaluation.model`
- `site/backend/data/evaluation/**` — bundled source PDFs for the Create Data batch flow

Frontend:

- `site/frontend/DESIGN.md` — UI element mapping (design tokens, button/modal system,
  consistency rules). **Source of truth before adding any UI; reuse, don't invent.**
- `site/frontend/src/App.jsx` (consumer chat conversation; state via
  `useReducer`, reducer extracted to `src/state/reducer.js`)
- `site/frontend/src/sections/DeveloperMode.jsx` (protected Admin Dashboard
  Developer Mode: the same chat workspace plus the admin-only Developer Tools)
- `site/frontend/src/state/reducer.js` — the extracted reducer (adds a `WORKFLOW_BLOCKED`
  action + `blocked` field); `src/state/transcript.js` projects `state.agents/logs/result/blocked`
  into chat messages + stepper nodes
- `site/frontend/src/hooks/useAgentRegistry.js` — **module-level singleton** that fetches
  `/api/agents` once; exports `useAgentRegistry()` hook (React) and `getDerivedRegistry()`
  sync accessor (for reducers). All pipeline keys, labels, and routing derive from this.
- `site/frontend/src/hooks/useWorkflow.js` (status polling; dispatches `WORKFLOW_BLOCKED`),
  `useAgents.js` (triggers registry load), `useCreateData.js`, `useFileUpload.js`
- `site/frontend/src/utils/` — shared `api.js` (HTTP client, same-origin access
  headers, and persistent paid-start idempotency attempts), `abortableSleep.js`,
  `ids.js`, `files.js` (file `getViewerType` dispatch + download URL helpers)
- `site/frontend/src/config/site.js` — public site configuration, including the
  PayPal donation recipient and URL builder
- `site/frontend/src/components/modals/` (one `Modal.jsx` shell; `eval/` holds the
  split Evaluator-results pieces; `useFileViewer.jsx` wires the generic file viewers)
- `site/frontend/src/styles/app.css` (design tokens in `:root`)

Tooling / analysis:

- `deploy/backup_restore.py` — root-run, fail-closed Pi backup/restore control
  plane used by `deploy/BACKUP_RESTORE.md`; shares the deployment lock, verifies
  quiescence and archive provenance, journals restore swaps, and rotates the
  restored browser token before reopening services
- `site/backend/src/tools/professor_comparison.py` — bundles per-batch eval CSVs into
  `<batch>/XLSX/compiled.xlsx` and computes judge ↔ professor κ / confusion /
  disagreements, appending an `Agreement` sheet. Exposed as CLI
  `main.py compare-professor <batch>` and API
  `POST /api/outputs/{label}/compare-professor` (surfaced after Batch
  Evaluation via `CompareProfessorPanel.jsx`; its input folder must contain
  evaluation spreadsheets).
- `site/backend/data/professor_comparison/Evaluation.xlsx` — professor's hand-graded
  ground-truth grid; `source_classification.json` (same dir) holds the professor's
  conceptual/abstract/numerical source-question classification keyed by source-PDF stem.
- `site/pyproject.toml` — ruff + pytest config (line length 110, target py310)

## Commands That Work

`backend` and `frontend` live under `site/`, and the backend resolves its repo
root as `site/` (so `.env`, `dist/`, and the venv all belong there). **Run these
from the `site/` directory.**

One-time setup:

```bash
cd site
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r backend/requirements-dev.txt
npm -C frontend ci
```

`requirements-dev.txt` includes the production lock plus pytest, Ruff, and
audit tooling. Production-only deployment uses `backend/requirements.txt`.

Full app locally (frontend served from `site/dist/`; build it first with
`npm -C frontend run build`):

```bash
cd site
.venv/bin/python -m uvicorn backend.src.server:app --host 127.0.0.1 --port 8000
```

Available at `http://127.0.0.1:8000`.

Frontend-only Vite dev server (from `site/`, proxies `/api` and
`/version.json` to `http://127.0.0.1:8000`; override with
`VITE_BACKEND_URL`):

```bash
npm -C frontend run dev:ui
```

CLI workflow (from `site/`; `archive/` stays at the repo root, hence `../`):

```bash
cd site
.venv/bin/python -m backend.src.main "../archive/Question Data Files/PDF/Quiz_Compilation.pdf" -n 2 -i "Make questions harder"
```

Tests + lint (from `site/`):

```bash
cd site
OPENAI_API_KEY= APP_ACCESS_TOKEN= .venv/bin/python -m pytest  # backend, offline
.venv/bin/python -m ruff check .   # backend lint
npm -C frontend test               # frontend (vitest)
npm -C frontend run lint           # frontend (eslint)
```

Judge ↔ professor agreement on a generated batch (the batch label is a folder
under `site/backend/outputs/`):

```bash
cd site
.venv/bin/python -m backend.src.main compare-professor <batch-output-folder> [--source physics_1_set]
```

## Git Workflow

- After completing a full feature, update, or significant upgrade, create a
  git commit before finalizing. Stage only the changes from that completed work
  and use a concise, descriptive commit message that captures the user-facing
  behavior and major implementation scope.
- Prompt edits are versioned separately. After any change to
  `site/backend/data/prompts/*.txt`, immediately run (from `site/`)
  `.venv/bin/python scripts/prompt_versions.py snapshot -m "<short prompt-change summary>"`
  so prompt experiments are saved as prompt-only commits that can be listed,
  inspected, or restored later.

## Environment Variables

The backend loads ignored `site/.env.local` before `site/.env`; an existing
process or systemd environment value has highest precedence. Arbitrary
`.env.*.local` mode files are not loaded. `.env.example` documents the common
runtime variables.

`site/.env` is **tracked in this repo** — the owner keeps it synced deliberately,
so do not re-add it to `.gitignore` or flag the committed key. This is an
explicit owner decision that overrides the general rule below; it does mean the
committed key is exposed in git history and should be rotated if the repository
is ever shared. Everywhere else, never put real secrets in tracked files,
frontend code, `VITE_*` variables, logs, screenshots, or chat. On Unix-like
systems, keep local environment files mode `0600`.

On Raspberry Pi, device secrets live in root-owned mode-`0600`
`/etc/uconn-isomorphic/environment`, outside releases. systemd loads release
defaults first, the persistent `config/admin-runtime.env` dashboard override
second, and this device environment last.

Required for real workflow execution:

- `OPENAI_API_KEY`

Required whenever the site is reachable beyond localhost:

- `APP_ACCESS_TOKEN` — shared API/browser gate sent as `X-App-Access-Token`;
  manual values must satisfy the 32-512 character server validation

Important optional app/runtime vars:

- `MAX_UPLOAD_BYTES`
- `MAX_UPLOAD_TOTAL_BYTES` — per-request total-size cap across all uploaded files (default 60 MB)
- Instructions/prompts have a non-configurable 20,000-character ceiling at
  every paid HTTP/CLI start boundary and in the engine guardrail for direct
  callers; rejection happens before provider work, idempotency hashing, or
  persistence.
- `UPLOAD_RETENTION_SECONDS` / `UPLOAD_MAX_BYTES` — automatic upload cleanup
  bounds (defaults 24 hours / 2 GiB). The byte bound is enforced live across
  concurrent `/api/upload`, Eval Batch, and Input-to-Agent source uploads;
  persisted abandoned sessions count immediately, and capacity exhaustion
  returns HTTP 507 before another write crosses the bound.
- `OUTPUT_RETENTION_SECONDS` / `OUTPUT_MAX_RUNS` / `OUTPUT_MAX_BYTES` —
  automatic output cleanup bounds (defaults 7 days / 500 runs / 20 GiB)
- `ARTIFACT_CLEANUP_INTERVAL_SECONDS` — cleanup cadence (default 3600 seconds)
- `WORKFLOW_TTL_SECONDS`, `WORKFLOW_MAX_ENTRIES`
- `ACTIVE_WORKFLOW_STALE_SECONDS` — processless non-terminal record timeout
  before status becomes `lost` (default 21600)
- `PRUNE_INTERVAL_SECONDS` (background prune cadence, default 60)
- `OPENAI_WORKFLOW_CONCURRENCY` — full agentic workflows allowed to execute at
  once (default 1)
- `MAX_ACTIVE_WORKFLOWS` — process-local admission cap for non-terminal
  workflow records (default 4). A shared atomic reservation pool covers direct
  starts and all Create Data children; reservations are released on every
  success, failure, and cancellation path.
- `OPENAI_WORKFLOW_BLOCKING_CONCURRENCY` — worker-thread bound for blocking
  SDK/filesystem calls (default 4)
- `OPENAI_AGENT_STAGE_TIMEOUT_SECONDS` / `OPENAI_GUARDRAIL_TIMEOUT_SECONDS` /
  `OPENAI_CONTAINER_TASK_TIMEOUT_SECONDS` / `OPENAI_EVALUATION_TIMEOUT_SECONDS`
  — fail-closed provider deadlines (defaults 900 / 120 / 600 / 900 seconds)
- `OPENAI_GUARDRAIL_STAGE_TIMEOUT_SECONDS` /
  `OPENAI_GUARDRAIL_MAX_CLASSIFIER_REQUESTS` — aggregate wall-clock and call
  budgets for each complete input/content classifier phase (defaults 300
  seconds / 16 requests)
- `OPENAI_PROVIDER_REQUEST_TIMEOUT_SECONDS` / `OPENAI_PROVIDER_MAX_RETRIES`
  — sync SDK transport timeout and retry cap (defaults 120 seconds / 0)
- `WORKFLOW_TOKEN_STREAMING_ENABLED=false` by default — legacy token-delta
  queues/streaming stay off because the frontend polls status
- `OPENAI_STATUS_TIMEOUT_SECONDS`
- `OPENAI_STATUS_CACHE_SECONDS` — single-flight success/failure cache for the
  public status probe (default 60; clamped to 5–300)
- `BATCH_WORKFLOW_CONCURRENCY` (default 2)
- `OPENAI_CONTAINER_ALLOWED_DOMAINS` — overrides `setup.allowedDomains` from `agents.json`
- OpenAI hosted-container networking requires both that request-level domain
  list and an organization-admin allowlist. The current six domains are
  `archive.ubuntu.com`, `security.ubuntu.com`, `deb.debian.org`,
  `security.debian.org`, `pypi.org`, and `files.pythonhosted.org`; a request
  fails if it names a domain outside the organization list. Keep both layers
  least-privilege and verify the current dashboard procedure against the
  [official shell-network guide](https://developers.openai.com/api/docs/guides/tools-shell#network-access).
- `MAX_GENERATED_ARTIFACT_BYTES` — streamed provider-generated PDF/TeX
  download cap (default 50 MiB; hard maximum 512 MiB)
- `OPENAI_FILE_EXPIRES_SECONDS` — fallback expiry for temporary provider file
  objects (default 86400; clamped to 3600–2592000)
- `OPENAI_AGENTS_DISABLE_TRACING` — tracing defaults to disabled; keep it
  disabled for course material except during an intentional sanitized debug session
- `GUARDRAILS_ENABLED` — toggles the three semantic/model checks (overrides
  `_config.guardrails.enabled`); structural input/content bounds remain active
- `GRADER_MODEL_OVERRIDE` — grade with a different model than `agents.json` `evaluation.model`
- `GRADER_MAX_COMPLETION_TOKENS` — generated-token ceiling for each score-model
  grader call (default 4096; strict range 256–16384)
- `GENERATION_STRATEGY` — generator strategy: `holistic` (default) or `decompose`
  (decompose-first; uses `prompts/generator_decompose.txt` and fills each generated
  question's `decomposition` field). See `workflow/agents.py::generation_strategy`.
- `GENERATOR_STYLE_REVIEW_ENABLED` — optional second high-reasoning generator
  wording pass; default `false`
- `MAX_SOURCE_QUESTIONS` / `MAX_GENERATED_QUESTIONS` /
  `MAX_EVALUATION_ITEMS` — per-run amplification caps (defaults 25 / 100 / 100)
- `MAX_EVAL_BATCH_ENTRIES` / `MAX_EVAL_BATCH_ITEMS` /
  `MAX_CREATE_DATA_ENTRIES` / `MAX_ACTIVE_BATCH_JOBS` — batch cost/admission
  caps (defaults 5 / 100 / 5 / 1)
- `DOWNLOAD_ZIP_MAX_SOURCE_BYTES` — uncompressed run-size cap before ZIP
  generation (default 134217728 / 128 MiB; hard maximum 536870912 / 512 MiB).
  ZIP creation also requires the terminal owner task to be fully finished and
  fingerprint-checks cached archives before reuse. The browser buffers the
  resulting ZIP blob, so raise the cap only for a deliberate, well-resourced
  client.
- `ENABLE_STAGE_CACHE` — test feature, default `false`. When truthy, reuses a prior
  stage's output (extracter, generator) keyed on the stage input + model + reasoning +
  prompt, persisted in `site/backend/runtime/stage_cache.sqlite3`. See
  `workflow/stage_cache.py`.
- `STAGE_CACHE_TTL_SECONDS` / `STAGE_CACHE_MAX_ENTRIES` /
  `STAGE_CACHE_MAX_RECORD_BYTES` — bounded cache controls (defaults `86400`,
  `128`, and `2097152`)
- `APP_RATE_LIMIT_REQUESTS` / `APP_RATE_LIMIT_WINDOW_SECONDS` — in-process
  IP-scoped cost-route throttle (defaults 12 requests / 60 seconds)
- `PAYPAL_DONATE_URL` — optional hosted HTTPS PayPal donation URL; never a PayPal secret
- `CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, `ADMIN_EMAILS` — production-only
  Cloudflare Access identity verification for `/admin` and `/api/admin/*`.
- `OPENAI_ADMIN_KEY` — optional Organization Owner key used only server-side for
  cost monitoring; `SMTP_*` + `ALERT_RECIPIENTS` configure incident email.
- `ADMIN_LOCAL_DEV_MODE`, `ADMIN_LOCAL_DEV_EMAIL` — tracked local dashboard
  test defaults. The bypass accepts only direct loopback requests; the Pi
  systemd unit explicitly overrides `ADMIN_LOCAL_DEV_MODE=false`.

## Architecture

### Backend

- FastAPI app factory in `site/backend/src/server.py`; routers under `site/backend/src/api/`.
  Launch with `uvicorn backend.src.server:app`.
- In-memory `workflows` and `batch_jobs` dicts in `site/backend/src/api/state.py`,
  mirrored to a SQLite recovery store (`site/backend/src/workflow/store.py`). Pruned by TTL +
  max entry count on a background interval (not on the request path). Startup
  reconciliation marks orphaned work `lost`/`errored`; shutdown terminalizes
  and cancels tracked work before closing the shared provider client.
- Uploads → `site/backend/uploads/<sessionId>/`.
- Run outputs → `site/backend/outputs/<timestamp>/`.
- If `site/dist/index.html` is missing, falls back to a simple HTML message.

### OpenAI agent runtime

- Full runs use two sequential, explicitly owned trust scopes rather than one
  shared mutable container. On an Extracter cache miss, the engine creates an
  extraction-only container, bootstraps document dependencies, then uploads and
  attaches source files. After the content guardrail accepts the structured
  output, it deletes that entire container and the provider file objects before
  creating a fresh downstream container with `file_ids=[]` for
  Verifier/Generator/LaTeX. A cleanup failure prevents downstream execution.
- Cache hits and replayed structured Extracter output create no extraction
  container; they are re-screened before a downstream container can exist.
  SDK auto-containers are disabled on production paths so every provider
  container belongs to the cancellation-safe cleanup ledger.
- Document setup uses `site/backend/data/prompts/bootstrap.txt` only on a real
  Extracter cache miss. TeX is installed lazily, only if absent, by
  `bootstrapCompile.txt` when a PDF is actually compiled. Both shell-task
  templates use `{var}` placeholders filled by `helpers.render_prompt`.
- Outbound domain allowlist comes from `agents.json` `setup.allowedDomains`;
  override at runtime via `OPENAI_CONTAINER_ALLOWED_DOMAINS`.
- Structured outputs are enforced via Pydantic `output_type=` on each Agent
  (`agents` SDK), not via `response_format`.
- Reasoning effort is set per-agent in `site/backend/data/agents.json`
  (currently `high` for the Generator, `medium` for the LaTeXifier/Evaluator/guardrails,
  `low` for the Verifier, and `none` for the Normalizer/Extracter).
- The five normal Agents SDK stages (Normalizer through LaTeXifier) use the
  engine's `run_llm_stage()` helper. It streams only when an `emit_token`
  callback is actually supplied; otherwise it uses `Runner.run`, and it maps
  `MaxTurnsExceeded` / `ModelBehaviorError` / `ModelRefusalError` to a clean
  per-stage `ValueError`. Evaluation uses the Evals integration, guardrails use
  their own bounded classifier calls, and runtime nodes are orchestration.
  Each normal stage gets an explicit `max_turns` (optional `agents.json`
  `maxTurns`; default 6, latexifier 12). Registry `maxOutputTokens` limits
  each normal-stage response (currently Normalizer 2048,
  Extracter/Verifier 32768, Generator/LaTeXifier 65536); setup shell responses
  are capped at 4096, guardrail responses at 2048, and Evals graders use
  `GRADER_MAX_COMPLETION_TOKENS`. Tool turns and retries can create additional
  responses, so these are not whole-run token budgets. Each active owned
  container has a drained heartbeat; latexifier expiry recovery creates one
  fresh downstream-only replacement without source file IDs.

### Guardrails

- `site/backend/src/workflow/guardrails.py` runs three checks: **G1** OpenAI
  Moderation (`omni-moderation-latest`) on the instruction/uploaded structured
  fields and on the complete structured extraction; **G2** a blocking
  jailbreak/prompt-injection classifier on the instruction and every uploaded
  structured agent-input string field that runs **before the container is
  created**; **G3** prompt-injection screening over every string in the
  complete Extracter output plus an academic/physics/on-topic/is-a-question
  classifier over the extracted question records.
- A detected violation trips terminal workflow status `blocked` (distinct from
  `error`): the engine sets `workflow["blocked"] = {category, reason, stage}` and
  marks the tripped guardrail node `blocked`. Current `failClosed: true` means
  guardrail infrastructure failures also block as a precaution; changing that
  policy must be an explicit deployment decision.
- Fresh runs, cached replays, and uploaded from-agent-input runs all pass the
  applicable raw-input and resolved-content guardrails. A modified prompt or
  structured artifact is screened again before downstream work.
- Before the first paid provider operation, the input guardrail locally
  validates every registry-owned prompt, the active generator variant, both
  container-task templates and their runtime placeholders, and the LaTeX
  template. Missing, unreadable, empty, or mismatched assets fail the workflow
  instead of producing a blank-instruction call.
- Raw PDF/DOC/image bytes are not locally interpreted before the Extracter
  reads them. The sandboxed extraction container has a strict domain allowlist
  and no application secrets; the content guardrail moderates and
  prompt-injection-screens every string in the complete structured extraction,
  while its academic classifier receives only question records. The engine then
  destroys the extraction container and uploaded provider files before
  Verifier/Generator execution in a fresh scope. Preserve this boundary unless
  a trustworthy local document parser is deliberately added.
- Config lives in `agents.json` `_config.guardrails`; classifier prompts are
  `data/prompts/{input,content}_guardrail.txt`; toggle the semantic/model checks
  with `GUARDRAILS_ENABLED` (structural bounds remain active).

### Token streaming (SSE)

- `WORKFLOW_TOKEN_STREAMING_ENABLED` defaults to `false`, so ordinary runs do
  not allocate per-workflow token queues or request streamed agent deltas. When
  explicitly enabled for a custom client, the engine emits
  `emit_token(agent_key, delta)` into a bounded queue and
  `GET /api/workflow/{id}/stream` can emit `token` `{agent, delta}` alongside
  `status` and `done`. The frontend does not consume token deltas; polling
  `/status` stays authoritative and includes `blocked`.

### Frontend

- React 19 + Vite 7; centralized `useReducer` state (reducer in `src/state/reducer.js`),
  wired in `App.jsx`. The consumer primary view is a **chat conversation**; its
  header site-mode dropdown is **User / Teacher** (persisted in `localStorage`
  `uiMode`) and only swaps the empty-conversation hint copy. The complete former
  Dev workspace now lives in the protected Admin Dashboard’s **Developer Mode**
  tab. All modals/features are preserved.
- Progress comes from **polling** `GET /api/workflow/{id}/status` (see `useWorkflow.js`),
  which stays authoritative and returns compact stage metadata rather than full
  agent input/output payloads. The frontend hydrates full agent details once
  from `/result` after completion. It no longer consumes the SSE `/stream`
  endpoint — while an agent works, the Stepper cycles a status word
  ("Thinking…", "Combobulating…", …) above that node and auto-scrolls it into view.
- All HTTP goes through `src/utils/api.js` (`requestJson`, `getJsonOrNull`,
  `fetchWithAbort`) — don't hand-roll `fetch` + error handling in hooks.
- Styling follows `site/frontend/DESIGN.md`: design tokens in `:root`, one `.btn`
  variant system, one `Modal.jsx` shell. Reuse variants and tokens; never add a
  one-off `*-btn` class or hardcode color/size/motion. Buttons in the same group
  share one height tier.
- The theme is a bright academic **UConn navy `#000E2F` + blue `#0A5BC4`** look;
  the accent is tokenized (`--color-primary-rgb` / `color-mix()`), so one accent
  edit re-themes the whole app. See `site/frontend/DESIGN.md` §2.
- **Compose from components, not raw elements (templates-first).** Feature code
  builds UI only from the reusable primitives in `site/frontend/src/components/ui/`
  (Button, IconButton, Card, TextInput/TextArea/Select, Dropdown, Checkbox,
  ToggleSwitch, Badge, StatusChip/ScoreChip/Pill, Chip, StatusDot,
  LogItem/LogList, EmptyState, Tabs, DataTable/KeyValueTable, Modal,
  AppShell/PageHeader/Banner/Footer, plus the chat primitives
  Avatar/ChatMessage/ChatTranscript/TypingIndicator/Composer/Stepper) — not
  from raw `<button className="btn …">` / visible `<input>` / ad-hoc class
  strings. Hidden native file inputs behind a visible primitive are the narrow
  file-picker seam documented in DESIGN.md. An ESLint rule forbids raw
  `.btn`/`.card`/control
  class strings in `components/**` outside `ui/`. Primitives are thin wrappers over the
  documented class contract. If a genuinely new element is needed, add it
  as a `ui/` primitive (+ a showcase specimen + a `ui/ui.test.jsx` assertion)
  **first**, then use it. See `site/frontend/DESIGN.md` §14–§15.
- **Design-system showcase** lives at `site/frontend/design-system.html` (a second
  Vite entry; `npm -C site/frontend run dev` → `/design-system.html`). It renders the
  real `ui/` primitives, organized Foundations → Components → Patterns, and is
  the living catalog to review building blocks before composing the site.
- Canonical spelling is `extracter` everywhere. No aliases exist in `agents.json`;
  callers must use canonical key names.
- All pipeline keys, labels, and routing come from `useAgentRegistry()` —
  never hardcode agent key strings in JS files.
- The frontend renders all 10 pipeline nodes (including `setup`, `output`, and the
  two guardrail nodes) because it derives its key list from the registry. The chat
  view's `Stepper` renders all 10 nodes (guardrail = shield, runtime = computer, with
  `blocked` status styling).
- **File viewing is generic, not per-file.** Result files are viewed through
  four viewers dispatched by `getViewerType` (`utils/files.js`): PDF, CSV, `.tex`,
  and a JSON/plain-text fallback (`JsonTextViewerModal`). Wire them via the
  `useFileViewer` hook (`modals/useFileViewer.jsx`) — don't add a new modal per
  file type. Call `useFileViewer(workflowId)` and render `viewerModals` once in
  the host component's JSX.
- **Modals stack.** `Modal.jsx` tracks a global open-modal stack: each open
  modal gets `z-index: 1000 + depth`, only the bottom modal darkens the page,
  and stacked backdrops are blur-only (`.is-stacked`). Never set z-index on a
  modal or restyle `.modal-backdrop` per component (see DESIGN.md §8 Stacking).
- **Three rich button-opened modals live in `ResultsSection`** (titled
  Results / Evaluations / Agents), separate from the generic dispatch above:
  - `FinalResultModal` ("Results") — tabbed viewer (Config / Source Qs /
    Generated Qs / Evaluation) for `final_result.json`; uses `useFileContent`.
    The Source Questions tab reads `verified_questions` (the Verifier fills in
    `is_correct`/`explanation`/`distractor_logic`; raw `source_questions` from
    the extracter leave them empty). The Evaluation tab derives each
    criterion's Pass/Fail from `score >= passing_score_threshold` — the report
    has `pass_rate`/`items_passed`, not a `passed` boolean.
  - `EvalResultsModal` ("Evaluations") — full evaluator results; sub-components
    in `eval/`: `EvalSummaryStrip`, `EvalItemsTable`, `EvalAggregateTable`.
    Criterion chips in `EvalItemsTable` are `<button>` elements; clicking one
    opens `CriterionReasoningModal` with the grader's step-by-step reasoning.
  - `WorkflowAgentsModal` ("Agents") — run summary (input files, normalizer
    config from `final_result.json`, extracted/generated counts), pipeline
    agent cards with metadata + flow display, per-agent Input/Output/Prompt
    buttons, and footer links that open the Results / Evaluations modals.
- **Runtime nodes have dedicated modals**: `SetupModal` ("Starting Runtime" —
  OpenAI runtime, workflow id, container packages/domains from the registry)
  and `OutputModal` ("Ending Runtime" — output files with View/Download).
  Both show the run's inputs from `state.runInputs`, captured by the reducer
  at `WORKFLOW_START` because the upload list clears when a run starts.
- **Grader reasoning lives at `results[].sample`** in the eval JSONL, not at
  the top-level `grader_samples` key. Use `parseSampleSteps(sample)` (exported
  from `modals/eval/evalData.js`) to parse `sample.output[0].content` (JSON
  string: `{result, steps: [{conclusion, description}]}`).
- **Normalize eval criterion keys** with `normalizeCriterionName`
  (`modals/eval/evalData.js`) before keying grades/passes/samples. Backend keys
  carry a UUID suffix; mixing raw and normalized keys renders duplicate chips.

## Code Conventions

- **Data files are hand-curated.** Files under `site/backend/data/` (`*.json`,
  `prompts/*.txt`, `latex_template.tex`) are managed by the user. Don't delete,
  rename, or rewrite them; only edit content when explicitly asked.
- **Prompt files must stay easy to roll back.** Before prompt experiments, use
  `.venv/bin/python scripts/prompt_versions.py status`; after editing prompts,
  snapshot them immediately with
  `.venv/bin/python scripts/prompt_versions.py snapshot`. Use
  `list`, `show <commit> <prompt>`, and `restore <commit> [prompt...]` from the
  same helper to inspect or switch versions.
- **Agent key is `latexifier`** in all configs and external surfaces (artifact
  filenames, agent dict keys, `agents.json`). Internal Python identifiers may use
  `latex` for brevity — keep it consistent within a file.
- **The engine never touches HTTP/CLI state directly.** The server passes a
  `touch_workflow` callback and a dict; the engine mutates that dict through the
  callback. Don't import FastAPI types into `workflow/`.
- **Reuse the engine's per-stage helpers** (`resolve_upstream_agent`,
  `finalize_agent_run`) instead of copy-pasting a stage block.
- **No blocking I/O in async paths.** Filesystem writes inside
  `workflow/engine.py` go through the engine's `to_thread` parameter.
- **No module-load side effects.** Don't read JSON/TXT at import time; use lazy
  accessors (`functools.lru_cache`) or first-call loaders.
- **Paths come from `paths.py`.** Don't recompute `REPO_ROOT`, `DATA_DIR`, etc.
- **No `try/except ImportError` dances in new files.** The bootstrap shim makes
  `from backend.src.x import ...` work in both package and script modes.
- **Tests run from `site/`** with absolute imports (`from backend.src.x ...`).
- **API routers import shared state from `api/state.py`.** Routers hold only
  route handlers; registries, config, helpers, and run wrappers live in `state.py`.
- **PayPal donation configuration.** The main-site header's Donate action uses
  `PAYPAL_DONATION_EMAIL` / `PAYPAL_DONATION_URL` from
  `site/frontend/src/config/site.js`. To transfer donations to a professor or
  another maintainer, change `DEFAULT_PAYPAL_DONATION_EMAIL` there or set the
  public build-time `VITE_PAYPAL_DONATION_EMAIL` override, then rebuild the
  frontend. Keep the recipient out of component markup so this remains a
  one-value change.
- **Chat/developer synchronization contract.** `App.jsx` is the source of truth
  for consumer chat behavior. The protected `DeveloperMode.jsx` must compose the
  same `ChatConversation` component (never a fork) and be verified alongside the
  consumer chat after shared changes. `DeveloperToolsPanel.jsx` is intentionally
  admin-only; when changing its controls or workflow wiring, update and verify
  the protected Developer Mode in the same change.
- **Registry-owned agent collections come from `registry.py`.** Never duplicate
  full sequences, runnable/stoppable sets, display names, models, aliases, or
  routing maps in Python; use `backend.src.registry` accessors such as
  `agent_sequence`, `llm_agents`, and `stoppable_agents`. Stage-specific
  orchestration may branch on a canonical key literal when that branch
  implements the stage itself, but the key must exist in and be validated
  against the registry. `artifacts.py` re-exports sequences/names only for
  legacy callers; the source remains `registry.py`.
- **Production frontend agent collections come from `useAgentRegistry()` /
  `getDerivedRegistry()`.** Don't duplicate production pipeline sequences,
  labels, or routing maps in feature modules. Tests and design-system specimens
  may use small canonical fixtures; stage-specific modal identifiers are UI
  state, not a replacement registry.
- **Frontend follows `site/frontend/DESIGN.md`** (see the Frontend notes above).

### Pipeline parallelism

- Within one workflow the stages are sequential (each consumes the previous output).
- Multi-file uploads in one workflow are intentionally combined into one dataset
  (the extracter prompt handles this).
- Batch fan-out uses `BATCH_WORKFLOW_CONCURRENCY=2`, but full workflows also
  pass through the process-wide `OPENAI_WORKFLOW_CONCURRENCY=1` default, so the
  default Pi executes only one full agentic workflow at a time. Eval-only batch
  entries use the batch bound directly.

## Workflow Stages

All 10 nodes are defined in `site/backend/data/agents.json` and driven by the engine
(6 agent + 2 runtime + 2 guardrail registry nodes):

0. `setup` *(runtime)* — owns the provider-resource lifecycle: after G2 and
   Normalizer, an Extracter cache miss gets an isolated document container;
   after G3 that scope is destroyed and a clean downstream container is created
   only if needed. TeX provisioning is deferred until compile.
0.5. `input_guardrail` *(guardrail)* — moderation + jailbreak/prompt-injection
   screening on the instruction and every string in an uploaded structured
   agent input, before any container
1. `normalizer` — parses the user prompt into structured config
2. `extracter` — reads uploaded files and extracts source questions
2.5. `content_guardrail` *(guardrail)* — moderation and prompt-injection
   screening over every Extracter-output string, plus an academic/physics/
   on-topic/is-a-question check over the extracted question records
3. `verifier` — validates answers, fills in explanations / `distractor_logic`
4. `generator` — creates isomorphic questions
5. `latexifier` — produces LaTeX and triggers PDF compilation in the container
6. `evaluation` — optional paid OpenAI Evals stage; writes the report + spreadsheet +
   rubric snapshot when explicitly enabled
7. `output` *(runtime)* — writes `final_result.json`, `agent_outputs/` artifacts, file metadata

A tripped guardrail ends the run in the terminal **`blocked`** status (distinct from `error`).

## API Surface

Routes live in `site/backend/src/api/` (registered on the app in `server.py`):

- `GET /api/health`, `GET /api/openai/status`, `GET /api/service-status`, `GET /version.json`
- `GET /api/public-config` (public auth-required flag + validated optional donation URL)
- `GET /api/readiness` (protected readiness booleans; never returns secrets)
- `GET /api/deployment/status` (protected, sanitized Pi release/update status)
- `POST /api/visitor/heartbeat` — anonymous five-minute active-visitor estimate;
  stores only a salted identifier hash.
- `/admin` — React operations dashboard. In production Cloudflare Access must
  protect `/admin*`; in local development the tracked loopback-only test mode
  may authenticate it without Cloudflare.
- `/api/admin/*` — Cloudflare Access JWT-protected operations API (dashboard,
  approved-root file browser/download/ZIP, maintenance/cache/key controls, audit,
  incidents, and Pi probe report); never exposes credentials or arbitrary paths.
  The approved `data` root is limited to `prompts/*.txt`, `agents.json`,
  `latex_template.tex`, and `rubric.json`.
- `POST /api/upload` (5 files max, 20 MB each by default; documents, images,
  text/markdown, and LaTeX — see `ALLOWED_UPLOAD_EXTENSIONS` in `api/state.py`)
- `GET /api/dropdown`, `GET /api/rubric`, `GET /api/agents`
- `GET /api/prompts/{prompt_name}`
- `GET /api/evaluation/files`
- `POST /api/workflow/start`
- `POST /api/workflow/{workflow_id}/stop`
- `POST /api/workflow/rerun-from-agent`, `POST /api/workflow/start-from-agent-input`
- `GET /api/workflow/{workflow_id}/status` (payload includes a `blocked` field;
  a guardrail-tripped run has terminal status `blocked`)
- `GET /api/workflow/{workflow_id}/result`
- `GET /api/workflow/{workflow_id}/stream` (SSE; emits named events `token` `{agent,delta}`,
  `status`, and `done` — no longer consumed by the frontend UI; polling `/status` stays authoritative)
- `GET /api/download/{workflow_id}/{file_path}`
- `GET /api/download-zip/{record_id}` (zips a workflow, Create Data batch, or
  Eval Batch output folder only after its owning task finishes; cached archives
  are rebuilt when their artifact fingerprint is stale)
- `GET /api/output-artifact?folder={label}&file={relative_path}` (contained,
  protected download for generated batch artifacts such as comparison workbooks)
- `POST /api/create-data/start`, `POST /api/create-data/{batch_id}/stop`
- `GET /api/create-data/{batch_id}/status`
- `GET /api/create-data/{batch_id}/result`
- `POST /api/outputs/{label}/compare-professor` (judge ↔ professor agreement
  for a batch folder containing evaluation spreadsheets; optional `sourceStem`
  form field for category averages)
- `GET /api/eval-batch/outputs`, `POST /api/eval-batch/start`
- `GET /api/eval-batch/{batch_id}/status`, `GET /api/eval-batch/{batch_id}/result`,
  `POST /api/eval-batch/{batch_id}/stop`

All five paid start routes (`workflow/start`, both workflow replay starts,
`create-data/start`, and `eval-batch/start`) require a client-generated
`X-Idempotency-Key`. The server binds its canonical request fingerprint to the
original workflow/batch ID in SQLite for at least seven days and while that
resource record remains retained; identical concurrent or post-restart
submissions return the original start response, while reuse with different
inputs returns HTTP 409. At exclusive single-process startup, pending claims
with a durable resource are committed and claims interrupted before resource
persistence are released. Frontend code creates the key before the POST,
persists pending retry metadata in tab-local `sessionStorage`, never
automatically retries a paid POST, and clears the key only after a validated
resource ID or a definitive rejection.

Eval-batch Stop is cooperative and best-effort: it cancels queued/local work,
but an already-running remote OpenAI evaluation may complete after the request.

When `APP_ACCESS_TOKEN` is configured, protected API clients must send the
matching `X-App-Access-Token`. Public health/bootstrap GETs are limited to
`/api/health`, `/api/openai/status`, `/api/public-config`, and `/version.json`.
The shared token is not per-user authentication or an admin role.

## Evaluation System

Lives in `site/backend/src/evaluation.py`.

> **Release deadline — OpenAI Evals shutdown.** The OpenAI Evals platform that this
> entire grading pipeline is built on (`client.evals.create`, `score_model`
> graders) goes **read-only on October 31, 2026 and shuts down November 30,
> 2026** per the
> [official OpenAI deprecations page](https://developers.openai.com/api/docs/deprecations).
> Assign and validate a replacement evaluation path before the read-only
> deadline. Generation may run with evaluation disabled, but evaluation
> batches/reports depend on this migration.

Current behavior:

- Rubric: `site/backend/data/rubric.json` (pass/fail scale, **8 criteria**). Grader model
  comes from `agents.json` `evaluation.model` (currently `gpt-5.6-luna`), overridable via env
  `GRADER_MODEL_OVERRIDE`. Reasoning effort is **per-criterion** (rubric.json per-criterion
  `reasoning`): `physics_correctness` + `distractor_structure_preservation` = high,
  `preserve_physical_content` + `solvable_and_specified` = medium, the rest inherit `low`.
- Each question-level criterion is scored as 0/1 (or -1 for N/A on criteria
  with `na_allowed: true`).
- Evaluation is opt-in for ordinary web/CLI generation. Eight rubric criteria
  mean approximately eight grader calls per evaluated item, in addition to
  workflow costs.
- Upload-mode Eval Batch isolates every untrusted source/generated PDF in a
  fresh extraction container. For `F` PDFs it projects `F` document-bootstrap
  calls and up to `2 × F` Extracter calls (one bounded retry per PDF), in
  addition to the `8 × N` grader fan-out. The default maximum is six PDFs: one
  source plus five generated entries. Moderation/content classifiers also run
  per extracted PDF and have variable batching.
- Reproducibility: every grader is seeded with `GRADER_SEED = 7` (surfaced in the report
  as `grader_seed`).
- Each score-model grader call has a generated-token ceiling from
  `GRADER_MAX_COMPLETION_TOKENS` (default 4096).
- Source `distractor_logic` labels are now fed to the judge (the item schema gains a
  `source_distractor_logic` field).
- A `FAILURE_TAGS` taxonomy: the grader prefixes a `[TAG]` on failing items; the report
  adds `reasoning_effort_by_criterion` and per-criterion `failure_tag_counts`. The grader
  system prompt (`evaluator.txt`) carries anti-bias + instruction-hierarchy framing.
  `parse_sample_steps` is exported for reuse.
- Aggregation reports `na_count` and `na_rate` per criterion; a
  `coverage_mismatch` is recorded under `deterministic_checks` when the eval
  run returns fewer items than were submitted.
- `validate_distractor_logic_preservation` (pipeline_validation.py) is a
  deterministic pre-judge check that every source `distractor_logic` label
  appears in the generated option set.

### Meta-evaluator (grader self-improvement)

`site/backend/src/tools/meta_evaluator.py` is a **PROPOSE-ONLY** CLI: it mines grader
rationales + `FAILURE_TAGS` + deterministic checks and emits ranked, file-targeted
proposals to improve the grader. It has a reward-hacking guard, an optional labeled
κ-gate (`--labels` / `--kappa-floor`) requiring at least five non-N/A comparisons
per criterion by default (`--min-kappa-samples`), an experiment ledger
(`site/backend/runtime/meta_eval_ledger.jsonl`), and a versioned calibration format
(`site/backend/data/calibration/*.jsonl`). Reports record prompt/rubric SHA-256
content hashes, including dirty content. Treat grader self-tags as hypotheses
unless deterministic checks or independent professor labels corroborate them.
It **never auto-applies** changes. It is
wrapped by the `.agents/skills/grader-review/SKILL.md` skill (given grader outputs → recommends
prompt/schema tweaks).

Per-run artifacts under `site/backend/outputs/<timestamp>/`:

- `final_result.json`, `agent_outputs/agent_metadata_output.json`
- `input_files/` — for accepted runs that reach source archival, portable,
  collision-safe snapshots that preserve the source files' real suffixes
- `agent_outputs/<agent>/agent_input.jsonl` + `agent_output.jsonl` (per attempt)
- `isomorphic_questions.tex`, `isomorphic_questions.pdf`
- `evaluation_outputs/evaluation_report.json`
- `evaluation_outputs/evaluation_dataset.jsonl`
- `evaluation_outputs/evaluation_agents.jsonl` (raw judge results)
- `evaluation_outputs/evaluation_spreadsheet.csv`
- `evaluation_outputs/rubric.json` (snapshot)

Per-batch (a timestamped batch folder under `site/backend/outputs/`):

- `batch_summary.json` at the batch root (create-data batches)
- After running `main.py compare-professor <batch>` (or the
  `POST /api/outputs/{label}/compare-professor` route): `XLSX/compiled.xlsx`
  (`Summary` + per-entry sheets + `Agreement` sheet), `XLSX/judge_agreement_report.json`,
  and `XLSX/judge_disagreements.csv`

## Batch / Create Data Flow

UI's "Create Data" flow batch-runs over files in `site/backend/data/evaluation/`.
It requires explicit approval for up to `MAX_CREATE_DATA_ENTRIES` full
generation workflows. It does not run rubric evaluation; use Batch Evaluation
separately before professor comparison.

- `POST /api/create-data/start`
- `GET /api/create-data/{batch_id}/status`
- `GET /api/create-data/{batch_id}/result`

Concurrency controlled by `BATCH_WORKFLOW_CONCURRENCY` (default 2).
Outputs are nested workflow output folders under a timestamped batch directory.

## Important Files To Edit For Common Tasks

| Task | Files |
|---|---|
| Change models / reasoning / tool usage / themes / `maxTurns` / `maxOutputTokens` | `site/backend/data/agents.json` |
| Add or rename an agent | `site/backend/data/agents.json` → `site/backend/src/registry.py` (new accessor if needed) |
| Change guardrail config / behavior | `site/backend/data/agents.json` (`_config.guardrails`), `site/backend/src/workflow/guardrails.py`, `site/backend/data/prompts/{input,content}_guardrail.txt` |
| Change container packages or domains | `site/backend/data/agents.json` (`setup.containerPythonPackages`, `setup.allowedDomains`) |
| Add a registry constant Python needs | `site/backend/src/registry.py` |
| Change agent behavior / instructions | `site/backend/data/prompts/*.txt` |
| Workflow orchestration | `site/backend/src/workflow/engine.py`, `site/backend/src/workflow/payloads.py`, `site/backend/src/main.py` |
| API routes / server state | `site/backend/src/api/**`, `site/backend/src/server.py` |
| Deterministic pipeline validation | `site/backend/src/pipeline_validation.py` |
| Eval scoring / reporting (seed, per-criterion reasoning, failure tags) | `site/backend/src/evaluation.py`, `site/backend/data/rubric.json` |
| Grader self-improvement proposals | `site/backend/src/tools/meta_evaluator.py`, `.agents/skills/grader-review/SKILL.md` |
| Judge ↔ professor agreement | `site/backend/src/tools/judge_agreement.py`, `site/backend/src/tools/professor_comparison.py`, `site/backend/data/professor_comparison/` |
| Frontend UX / state | `site/frontend/src/App.jsx`, `site/frontend/src/state/reducer.js`, `site/frontend/src/state/transcript.js`, `site/frontend/src/hooks/**`, `site/frontend/src/components/**` |
| Chat surface / process rail | `site/frontend/src/sections/ChatConversation.jsx`, `site/frontend/src/components/ui/Chat.jsx` (Avatar/ChatMessage/ChatTranscript/TypingIndicator/Composer), `site/frontend/src/components/ui/Stepper.jsx` (incl. the cycling thinking words + auto-scroll); compose this shared surface in both `App.jsx` and protected `sections/DeveloperMode.jsx` |
| Protected developer tools | `site/frontend/src/sections/DeveloperMode.jsx`, `site/frontend/src/components/DeveloperToolsPanel.jsx`, and the matching developer modals/hooks |
| Frontend styling / design system | `site/frontend/DESIGN.md`, `site/frontend/src/styles/app.css` |
| Add/extend a UI primitive (template) | `site/frontend/src/components/ui/*` + `ui/index.js`, then `site/frontend/src/design-system/manifest.jsx` + `ui/ui.test.jsx` + `site/frontend/DESIGN.md` §14 |
| Design-system showcase page | `site/frontend/design-system.html`, `site/frontend/src/design-system/**`, `site/frontend/vite.config.js` (multi-page input) |
| File viewer dispatch | `site/frontend/src/utils/files.js` (`getViewerType`), `site/frontend/src/components/modals/useFileViewer.jsx` |
| Rich result modals | `site/frontend/src/components/modals/FinalResultModal.jsx`, `EvalResultsModal.jsx`, `WorkflowAgentsModal.jsx` |
| Runtime node modals | `site/frontend/src/components/modals/SetupModal.jsx` ("Starting Runtime"), `OutputModal.jsx` ("Ending Runtime") |
| Modal shell / stacking / backdrop | `site/frontend/src/components/modals/Modal.jsx`, DESIGN.md §8 |
| Eval criterion reasoning | `site/frontend/src/components/modals/eval/evalData.js` (`parseSampleSteps`), `eval/CriterionReasoningModal.jsx` |
| Add a bundled test PDF for Browse Test Files / Create Data | drop it in `site/backend/data/evaluation/` (listed dynamically by `GET /api/evaluation/files`) |

## Deployment Notes

Deploy scripts live in `deploy/` (`deploy.sh` for the systemd/uvicorn service,
`preview.mjs` for cross-platform local preview, `preview.sh` as the Unix-only
fallback, and `DEPLOY.md` for operations). Treat them as operational guides,
not source of truth for local development.

- The Pi updater deploys only commits fetched from the root-configured, pinned
  GitHub remote's `main` branch. Do not make repository edits on the Pi and
  expect them to be deployed; publish reviewed work to that pinned branch
  instead.
- Production uses immutable releases under `releases/<commit>` plus a `current`
  symlink. It retains the active release and three prior good releases, and
  rolls back automatically when the candidate cannot pass the backend health
  check.
- `site/backend/uploads/`, `site/backend/outputs/`, and `site/backend/runtime/`
  are linked to persistent Pi storage in every release. Never delete, rename,
  or make incompatible schema changes to these paths without an explicit
  migration and rollback plan.
- `GET /api/deployment/status` is the sanitized, dashboard-safe deploy report;
  use the updater's systemd journal for detailed diagnostics.
- The installer writes a persistent `config/admin-runtime.env` override and runs
  `${SERVICE_NAME}-operations.timer`; the web service has no shell/systemd access.
  The systemd unit reads the release `.env`, then this persistent override, then
  the root-owned device environment, and forcibly sets
  `ADMIN_LOCAL_DEV_MODE=false` after all of them; configure Cloudflare Access and
  Google separately from the tunnel installer.
- Device secrets are stored in `/etc/uconn-isomorphic/environment`; use
  `deploy.sh --configure-secrets`, `--rotate-openai-key`,
  `--rotate-access-token`, `--configure-donation`,
  `--configure-runtime-policy`, or `--status`, never a committed release file.
- On the installed Pi, every admitted paid web/CLI command holds a shared lease
  on `/run/uconn-isomorphic-work-lock/activity.lock` through provider and
  artifact cleanup. Deploy, backup, and restore take the conflicting exclusive
  lease before maintenance/restart work. Preserve that full-lifecycle lock
  contract; the maintenance marker alone cannot close the acceptance race.
- Deployment begins local-only. Public Cloudflare exposure requires a verified
  access boundary, rate limit, download authorization, manual backup/restore
  drill, Cloudflare **Always Use HTTPS** with an exact-host redirect, and a
  live Cloudflare Access denial/login response.
- There is no automatic backup/restore. Immutable-release rollback restores
  code, not persistent uploads, outputs, runtime databases, or secrets.

## Agent Warnings / Gotchas

- Use `site/backend/src/...` paths — not historical `src/...`.
- There is no root `package.json`; use `npm -C site/frontend <script>` from the
  repository root or `npm -C frontend <script>` from `site/`.
- `site/frontend/DESIGN.md` is the real frontend doc (the design system); there is no
  `site/frontend/README.md`.
- Canonical spelling is `extracter` everywhere. There are **no aliases** in
  `agents.json` — callers must use the exact key name.
- All 10 nodes appear in the frontend pipeline (including `setup`, `output`, and the
  two `kind: "guardrail"` nodes); the list is driven by `useAgentRegistry()`, not hardcoded.
  Guardrail checks execute inline, while their registry nodes are excluded from
  the runnable/stoppable/LLM sets.
- The SSE `/stream` endpoint still exists on the backend but is **not consumed by the
  frontend** (the matrix token-stream effect was removed); polling `/status` remains
  the authoritative status source.
- The consumer site has two modes — **User / Teacher** — via the header `.mode-select`
  dropdown (persisted in `localStorage` `uiMode`). The complete Developer Tools
  workspace is available only in the protected Admin Dashboard’s **Developer Mode**
  tab; keep the shared chat surface and developer workspace synchronized per the
  frontend contract above.
- A guardrail-tripped run ends in the terminal **`blocked`** status (distinct from `error`),
  surfaced as a top-level `blocked` field on the status payload.
- `dist/` is generated by `npm -C site/frontend run build`; it is gitignored.
- Workflow state is in-memory plus a SQLite mirror (`site/backend/runtime/`); active
  in-flight runs do not resume across a restart and are terminalized as
  `lost`/`errored` during startup reconciliation.
- `APP_ACCESS_TOKEN` is a shared deployment boundary, not student identity,
  role-based access control, or quota accounting. The Dev panel is a
  workflow/developer surface, not a full host-admin dashboard; the proposed
  admin dashboard is not implemented.
- The current rate limiter is in-process and resets on restart; there is no
  authoritative per-student or dollar-denominated semester budget.
- Ordinary uploaded document bytes are not locally parsed and quarantined
  before the tool-enabled Extracter sees them. The instruction and every
  uploaded structured agent-input string receive the early input checks; after
  extraction, every structured output string is moderated and injection-
  screened, while academic classification is question-only. The extraction
  container and provider files are destroyed after G3 so document state cannot
  flow into downstream tools, but that is containment rather than
  deterministic pre-parsing. Keep the upload surface trusted until a bounded
  local parsing/OCR quarantine stage is deliberately implemented.
- Provider-container document/TeX bootstrap uses allowlisted distro/PyPI
  repositories with direct Python versions pinned, but not a complete
  transitive hash lock. Do not describe it as equivalent to the hash-locked Pi
  application environment.
- No `LICENSE` has been selected. Do not choose or add one on the owner's
  behalf; professor handoff and external redistribution require an explicit
  owner licensing decision.
- Model ids are intentional and live in `agents.json`. The configured pipeline runs a **two-tier
  policy**: the **Generator** uses `gpt-5.6-terra` (high reasoning); **every other LLM
  stage and the grader** use `gpt-5.6-luna`. All three minimum classifier calls
  across the two guardrail nodes and the setup/compile shell orchestration also
  use the configured Luna model; `output` is local despite carrying registry
  metadata. Do not
  change this project policy merely because a different model is newer; make
  model/cost changes intentionally through the pipeline skill.
- The stable pre-overhaul baseline is tagged `stable-20260629`.
- `registry._load_raw` is `@lru_cache`. In tests that swap `AGENTS_CONFIG_PATH`,
  call `registry._load_raw.cache_clear()` first or the old data is returned.
  Editing `agents.json` requires a backend restart for the same reason.
- The frontend caches `/api/agents` at module level in `useAgentRegistry.js`
  (reload the page after agents.json changes). Vitest tests that render
  registry-driven components must stub `fetch` and `await loadAgentRegistry()`
  before rendering — use a current registry-driven component test as the pattern.

## Repository Skills

Project skills live under `.agents/skills/<name>/SKILL.md`; `.claude/skills`
points to the same root. Every skill has an immutable 32-character lowercase
hex ID, `agents/openai.yaml`, and local icon assets. Preserve IDs byte-for-byte
across updates and renames.

- `agent-pipeline-changes` — pipeline/registry change workflow
- `check-agents-registry` — deterministic registry consistency checks
- `grader-review` — propose-only evaluation failure review
- `run-tests` — complete backend/frontend/build/script verification
- `verify-frontend-ui` — real-browser frontend verification
- `handoff-readiness` — professor-release operational audit

## Hard Don'ts

- Don't import from `archive/` — nothing references it; it's the user's storage.
- Don't add "remove this later" comments or feature flags for code you can just
  remove now.
- Don't reintroduce the old `latex_syntax_quality` rubric criterion — it was
  removed intentionally.
- Don't mock the database or external state in `pipeline_validation` tests;
  integration tests must exercise the real validator.
- Don't duplicate registry-owned sequences, sets, labels, models, aliases, or
  routing maps in Python. Canonical key literals are allowed only in
  stage-specific implementation branches validated by the registry.
- Don't duplicate production pipeline sequences, labels, or routing maps in JS;
  derive them from `useAgentRegistry()` / `getDerivedRegistry()`. Small
  test/specimen fixtures and stage-specific modal identifiers are allowed.
- Don't add aliases back to `agents.json` — use canonical key names everywhere.
- Don't store agent constants (sequences, names, model ids, theme lists) in
  Python or JS files — they belong in `agents.json` and `registry.py`.

## Visual Changes, Mobile Review, And Cleanup

Every frontend visual change must be verified in the real rendered site. This
applies equally to desktop Codex, Codex Mobile, remote sessions, and similar
harnesses. Use the project `verify-frontend-ui` skill after frontend, layout,
CSS, interaction, or accessibility changes.

- Read `site/frontend/DESIGN.md`, run the relevant frontend tests, and drive the
  changed UI in a real browser. Use browser DOM measurements such as
  `getBoundingClientRect()` and `getComputedStyle()` for layout-sensitive
  behavior; screenshots are review evidence, not the only correctness check.
- When working through Codex Mobile, another remote client, or whenever the user
  asks to see the result, capture two to four focused screenshots that make the
  changed behavior easy to review on a phone. Include a phone viewport
  (`390x844`) and a desktop viewport (`1440x900`) unless the change is
  intentionally single-size.
- **Attach the screenshot image data directly to the chat.** A local filename,
  path, or statement that a screenshot was taken is not sufficient. With a
  Node-backed Playwright session, emit the screenshot buffer with
  `await nodeRepl.emitImage(buffer)`; with another browser tool, return its image
  result to the conversation. Caption each image with its route, viewport, and
  UI state.
- Exercise the exact affected state: open the relevant modal or tab and check
  hover, focus, loading, empty, error, completed, or responsive behavior when
  applicable. Verify both consumer chat and protected Developer Mode when a
  shared `ChatConversation` change can affect both. Test desktop, tablet, and
  narrow layouts; exercise the access gate, chat transcript, registry-derived
  Stepper, Composer, Dev sidebar, result/error/blocked states, and modal
  focus/stacking.
- Do not install an unpinned browser package ad hoc; report an unavailable
  browser gate instead.

For local preview, `npm -C site/frontend run dev:ui -- --host 127.0.0.1` is
enough for static layout work. Registry/API-backed surfaces also need the
backend running from `site/` on `127.0.0.1:8000`. The cross-platform full
preview launcher is `node deploy/preview.mjs --dev`.

Browser and process cleanup is a required final step, including after a failed
test or debugging attempt:

- Before launching anything, note the relevant existing browser windows,
  listeners, and processes. Track the exact browser handles, PIDs, and ports
  created during the task.
- Close every page, context, browser window, and browser process opened for the
  task. When controlling the user's existing Chrome, close only the tabs or
  windows created by the agent; preserve the user's pre-existing session.
- Stop every frontend, backend, preview launcher, watcher, and helper process
  started for the task. Terminate the recorded process tree when a launcher
  created child processes. Never kill a process merely because it occupies a
  familiar port.
- Confirm the recorded PIDs exited and that no new listener or browser window
  remains. If cleanup cannot be completed, report the exact remaining PID,
  port, tab, or window.
- Keep temporary captures under a workspace `tmp/` and only until they have been
  attached and inspected, then remove them unless the user explicitly asks to
  retain downloadable copies.
