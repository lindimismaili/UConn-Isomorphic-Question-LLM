# UConn Isomorphic Question Generator — AGENTS.md

Onboarding guide for coding agents (Claude, Codex, etc.) working in this repo.
**This file is the single source of truth for the repo** — `CLAUDE.md` just points
here. Older `.claude/CLAUDE.md` / `.codex/AGENTS.md` references are stale.

## Repository Docs

Every Markdown doc tracked in the repo:

- [README.md](README.md) — human-facing setup, environment, run/CLI/output reference
- [CLAUDE.md](CLAUDE.md) — Claude Code entry point; a pointer straight to this file
- [site/backend/BACKEND.md](site/backend/BACKEND.md) — backend reference: what every file does,
  key functions, the 10-node workflow, the HTTP API routes, `data/` files, and tests
- [site/frontend/DESIGN.md](site/frontend/DESIGN.md) — frontend design system: tokens, layout,
  buttons, modals, themes, transitions, consistency rules (**read before any UI change**)
- [deploy/DEPLOY.md](deploy/DEPLOY.md) — deployment + local preview scripts (operational, not source of truth)

## What This Repo Is

FastAPI + React app that generates isomorphic physics multiple-choice questions
from uploaded source documents using the OpenAI Agents SDK.

Full runtime pipeline (10 registry nodes):

`setup → input_guardrail → normalizer → extracter → content_guardrail → verifier → generator → latexifier → evaluation → output`

- `site/backend/data/agents.json` is the **single source of truth** for all 10 nodes plus
  global constants (`_config`). Python reads through `site/backend/src/registry.py`;
  the frontend derives from `GET /api/agents` via `site/frontend/src/hooks/useAgentRegistry.js`.
- `setup` and `output` are `kind: "runtime"` nodes; `input_guardrail` and `content_guardrail`
  are `kind: "guardrail"` nodes; the other six are `kind: "agent"` LLM calls. So the pipeline
  is **6 LLM agents + 2 runtime + 2 guardrail**.
- Guardrail nodes appear (and animate) in the pipeline visual but are **excluded from the
  runnable/stoppable/LLM sets** — `input_to_agent_sequence()`, `llm_agents()`, and
  `stoppable_agents()` all skip `kind == "guardrail"`.
- Both the FastAPI server and the CLI call the same workflow engine.

## Source-Of-Truth Paths

Backend:

- `site/backend/src/server.py` — FastAPI app factory (CORS, lifespan, static mount).
  Launch with `uvicorn backend.src.server:app` or `python -m backend.src.server`.
- `site/backend/src/api/` — route handlers split by domain (`health`, `config`,
  `files`, `workflows`, `batch`) plus `state.py` (in-memory registries, config,
  helpers, workflow-runner wrappers, background pruning loop)
- `site/backend/src/workflow/` — shared engine, agent factory, payload builders,
  resume/replay helpers, SQLite `store.py` (single source of truth for orchestration),
  and `guardrails.py` (the three moderation/jailbreak/on-topic guardrails)
- `site/backend/src/main.py` — CLI workflow orchestration (delegates to the engine)
- `site/backend/src/container.py` — shared OpenAI container bootstrap + LaTeX compile
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
  `countLabel`, `countField` (output array used for question counts), `aliases`,
  `inputFrom`/`outputTo` (flow display names), `cliDescription`, `resultField`, plus an
  optional `maxTurns` (per-agent turn cap; default 6, latexifier 12);
  the evaluation entry adds `promptName`; the setup entry adds `allowedDomains` +
  `containerPythonPackages`; `_config` holds `themes`, `apiTitle`, `apiDescription`, and
  `guardrails` (`enabled`, `model`, `moderationModel`, `reasoning`, `physicsMinConfidence`,
  `blockOffTopic`, `failClosed`, `maxQuestionsSampled`, `inputNode`, `contentNode`)
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
- `site/frontend/src/utils/` — shared `api.js` (HTTP client), `abortableSleep.js`,
  `ids.js`, `files.js` (file `getViewerType` dispatch + download URL helpers)
- `site/frontend/src/config/site.js` — public site configuration, including the
  PayPal donation recipient and URL builder
- `site/frontend/src/components/modals/` (one `Modal.jsx` shell; `eval/` holds the
  split Evaluator-results pieces; `useFileViewer.jsx` wires the generic file viewers)
- `site/frontend/src/styles/app.css` (design tokens in `:root`)

Tooling / analysis:

- `site/backend/src/tools/professor_comparison.py` — bundles per-batch eval CSVs into
  `<batch>/XLSX/compiled.xlsx` and computes judge ↔ professor κ / confusion /
  disagreements, appending an `Agreement` sheet. Exposed as CLI
  `main.py compare-professor <batch>` and API
  `POST /api/outputs/{label}/compare-professor` (surfaced in the frontend's
  Batch Eval + Create Data modals via `CompareProfessorPanel.jsx`).
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
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt
npm -C frontend install
```

Full app locally (frontend served from `site/dist/`; build it first with
`npm -C frontend run build`):

```bash
cd site
.venv/bin/python -m uvicorn backend.src.server:app --host 127.0.0.1 --port 8000
```

Available at `http://127.0.0.1:8000`.

Frontend-only Vite dev server (proxies `/api`, `/version.json`, `/data`
to `http://127.0.0.1:8000`; override with `VITE_BACKEND_URL`):

```bash
npm -C site/frontend run dev:ui
```

CLI workflow (from `site/`; `archive/` stays at the repo root, hence `../`):

```bash
cd site
.venv/bin/python -m backend.src.main "../archive/Question Data Files/PDF/Quiz_Compilation.pdf" -n 2 -i "Make questions harder"
```

Tests + lint (from `site/`):

```bash
cd site
.venv/bin/python -m pytest         # backend
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
  `python scripts/prompt_versions.py snapshot -m "<short prompt-change summary>"`
  so prompt experiments are saved as prompt-only commits that can be listed,
  inspected, or restored later.

## Environment Variables

`site/.env` (the backend's resolved repo root) is loaded manually by backend
code if values aren't already in the environment. `.env` is **tracked in the repo** (the user keeps
it synced deliberately — do not re-add it to `.gitignore` or flag the
committed key); `.env.example` documents the common runtime and dashboard
variables. For the
FastAPI server, `.env.local` / `.env.*.local` load first as ignored local
overrides; shell-provided values still win.

Required for real workflow execution:

- `OPENAI_API_KEY`

Optional app/runtime vars (see `.env.example` for the full list + defaults):

- `MAX_UPLOAD_BYTES`
- `MAX_UPLOAD_TOTAL_BYTES` — per-request total-size cap across all uploaded files (default 60 MB)
- `WORKFLOW_TTL_SECONDS`, `WORKFLOW_MAX_ENTRIES`
- `PRUNE_INTERVAL_SECONDS` (background prune cadence, default 60)
- `OPENAI_STATUS_TIMEOUT_SECONDS`
- `BATCH_WORKFLOW_CONCURRENCY` (default 2)
- `OPENAI_CONTAINER_ALLOWED_DOMAINS` — overrides `setup.allowedDomains` from `agents.json`
- `GUARDRAILS_ENABLED` — toggles the three guardrails (overrides `_config.guardrails.enabled`)
- `GRADER_MODEL_OVERRIDE` — grade with a different model than `agents.json` `evaluation.model`
- `GENERATION_STRATEGY` — generator strategy: `holistic` (default) or `decompose`
  (decompose-first; uses `prompts/generator_decompose.txt` and fills each generated
  question's `decomposition` field). See `workflow/agents.py::generation_strategy`.
- `ENABLE_STAGE_CACHE` — test feature, default `false`. When truthy, reuses a prior
  stage's output (extracter, generator) keyed on the stage input + model + reasoning +
  prompt, persisted in `site/backend/runtime/stage_cache.sqlite3`. See
  `workflow/stage_cache.py`.
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
  mirrored to a SQLite store (`site/backend/src/workflow/store.py`). Pruned by TTL +
  max entry count on a background interval (not on the request path).
- Uploads → `site/backend/uploads/<sessionId>/`.
- Run outputs → `site/backend/outputs/<timestamp>/`.
- If `site/dist/index.html` is missing, falls back to a simple HTML message.

### OpenAI agent runtime

- One shared Code Interpreter container per workflow; reused across agent calls.
- Container bootstrap installs LaTeX + PDF tooling via `site/backend/src/container.py`;
  the shell-task prompts live in `site/backend/data/prompts/bootstrap.txt` and
  `bootstrapCompile.txt` with `{var}` placeholders filled by `helpers.render_prompt`.
- Outbound domain allowlist comes from `agents.json` `setup.allowedDomains`;
  override at runtime via `OPENAI_CONTAINER_ALLOWED_DOMAINS`.
- Structured outputs are enforced via Pydantic `output_type=` on each Agent
  (`agents` SDK), not via `response_format`.
- Reasoning effort is set per-agent in `site/backend/data/agents.json`
  (currently `high` for the Generator, `medium` for the LaTeXifier/Evaluator/guardrails,
  `low` for the Verifier, and `none` for the Normalizer/Extracter).
- Every stage runs through the engine's `run_llm_stage()` helper: it streams via
  `Runner.run_streamed` when an `emit_token` callback is passed (server), else
  `await Runner.run` (CLI/tests), and catches `MaxTurnsExceeded` /
  `ModelBehaviorError` / `ModelRefusalError` → a clean per-stage `ValueError`.
  Each stage gets an explicit `max_turns` (optional `agents.json` `maxTurns`;
  default 6, latexifier 12). A container heartbeat + latexifier recreate-on-expiry
  keep the shared container alive.

### Guardrails

- `site/backend/src/workflow/guardrails.py` runs three checks: **G1** OpenAI
  Moderation (`omni-moderation-latest`) on the instruction + extracted text; **G2**
  a blocking jailbreak/prompt-injection classifier on the raw instruction that runs
  **before the container is created**; **G3** an academic/physics on-topic +
  is-a-question classifier on the extracter output.
- Fail-closed trips a **new terminal workflow status `blocked`** (distinct from
  `error`): the engine sets `workflow["blocked"] = {category, reason, stage}` and
  marks the tripped guardrail node `blocked`.
- Config lives in `agents.json` `_config.guardrails`; classifier prompts are
  `data/prompts/{input,content}_guardrail.txt`; toggle with env `GUARDRAILS_ENABLED`.

### Token streaming (SSE)

- The engine emits token deltas via `emit_token(agent_key, delta)` → a per-workflow
  queue in `api/state.py` → the SSE endpoint `GET /api/workflow/{id}/stream`, which
  emits named events `token` `{agent, delta}`, `status`, and `done`. The endpoint
  still exists, but the frontend **no longer consumes it** (the matrix token-stream
  effect was removed; the Stepper cycles a status word above the thinking node
  instead). **Polling `/status` stays authoritative** and its payload includes a
  `blocked` field.

### Frontend

- React 19 + Vite 7; centralized `useReducer` state (reducer in `src/state/reducer.js`),
  wired in `App.jsx`. The consumer primary view is a **chat conversation**; its
  header site-mode dropdown is **User / Teacher** (persisted in `localStorage`
  `uiMode`) and only swaps the empty-conversation hint copy. The complete former
  Dev workspace now lives in the protected Admin Dashboard’s **Developer Mode**
  tab. All modals/features are preserved.
- Progress comes from **polling** `GET /api/workflow/{id}/status` (see `useWorkflow.js`),
  which stays authoritative. The frontend no longer consumes the SSE `/stream`
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
  (Button, IconButton, Card, TextInput/TextArea/Select, ToggleSwitch, Badge,
  StatusChip/ScoreChip/Pill, Chip, StatusDot, LogItem/LogList, EmptyState, Tabs,
  DataTable/KeyValueTable, Modal, AppShell/PageHeader/Banner/Footer, plus the
  chat primitives Avatar/ChatMessage/ChatTranscript/TypingIndicator/Composer/
  Stepper) — not from raw `<button className="btn …">` /
  `<input>` / ad-hoc class strings. An ESLint rule forbids raw `.btn`/`.card`/control
  class strings in `components/**` outside `ui/`. Primitives are thin wrappers over the
  documented classes (no new CSS). If a genuinely new element is needed, add it
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
  `python scripts/prompt_versions.py status`; after editing prompts, snapshot
  them immediately with `python scripts/prompt_versions.py snapshot`. Use
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
- **All agent constants come from `registry.py`.** Don't hardcode agent key strings
  (`"normalizer"`, `"extracter"`, etc.) as literals in Python — import from
  `backend.src.registry` (`agent_sequence`, `llm_agents`, `stoppable_agents`, etc.).
  `artifacts.py` re-exports the sequence/names for legacy callers but the source is
  `registry.py`.
- **All frontend agent data comes from `useAgentRegistry()` / `getDerivedRegistry()`.** 
  Don't declare pipeline keys, labels, or routing maps in JS modules.
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
- **Frontend follows `site/frontend/DESIGN.md`** (see the Frontend notes above).

### Pipeline parallelism

- Within one workflow the stages are sequential (each consumes the previous output).
- Multi-file uploads in one workflow are intentionally combined into one dataset
  (the extracter prompt handles this).
- The batch endpoint runs workflows concurrently with `Semaphore(BATCH_WORKFLOW_CONCURRENCY=2)`;
  the eval-only batch endpoint (`/api/eval-batch/start`, CLI `main.py eval`) uses the same bound.

## Workflow Stages

All 10 nodes are defined in `site/backend/data/agents.json` and driven by the engine
(6 LLM agents + 2 runtime + 2 guardrail):

0. `setup` *(runtime)* — creates the shared OpenAI container; bootstraps LaTeX + Python deps
   (the G2 jailbreak guardrail runs **before** the container is created)
0.5. `input_guardrail` *(guardrail)* — moderation + jailbreak/prompt-injection check on the raw instruction
1. `normalizer` — parses the user prompt into structured config
2. `extracter` — reads uploaded files and extracts source questions
2.5. `content_guardrail` *(guardrail)* — moderation + academic/physics on-topic + is-a-question check on the extracter output
3. `verifier` — validates answers, fills in explanations / `distractor_logic`
4. `generator` — creates isomorphic questions
5. `latexifier` — produces LaTeX and triggers PDF compilation in the container
6. `evaluation` — runs OpenAI Evals; writes the report + spreadsheet + rubric snapshot
7. `output` *(runtime)* — writes `final_result.json`, `agent_outputs/` artifacts, file metadata

A tripped guardrail ends the run in the terminal **`blocked`** status (distinct from `error`).

## API Surface

Routes live in `site/backend/src/api/` (registered on the app in `server.py`):

- `GET /api/health`, `GET /api/openai/status`, `GET /api/service-status`, `GET /version.json`
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
- `POST /api/workflow/rerun-from-agent`, `POST /api/workflow/start-from-agent-input`
- `GET /api/workflow/{workflow_id}/status` (payload includes a `blocked` field;
  a guardrail-tripped run has terminal status `blocked`)
- `GET /api/workflow/{workflow_id}/result`
- `GET /api/workflow/{workflow_id}/stream` (SSE; emits named events `token` `{agent,delta}`,
  `status`, and `done` — no longer consumed by the frontend UI; polling `/status` stays authoritative)
- `GET /api/download/{workflow_id}/{file_path}`
- `GET /api/download-zip/{workflow_id}` (zips the whole output folder once,
  reuses the archive on later requests)
- `POST /api/create-data/start`, `POST /api/create-data/{batch_id}/stop`
- `GET /api/create-data/{batch_id}/status`
- `GET /api/create-data/{batch_id}/result`
- `POST /api/outputs/{label}/compare-professor` (judge ↔ professor agreement on any
  batch output folder; optional `sourceStem` form field for category averages)

## Evaluation System

Lives in `site/backend/src/evaluation.py`.

> **Tracked risk — OpenAI Evals shutdown.** The OpenAI Evals platform that this
> entire grading pipeline is built on (`client.evals.create`, `score_model`
> graders) goes **read-only on October 31, 2026 and shuts down November 30,
> 2026** per OpenAI's docs. No migration guide has been published yet. Do not
> rearchitect `evaluation.py` preemptively; plan a deliberate migration once
> OpenAI publishes a replacement (expected to be "Datasets"-based) or as the
> deprecation date approaches.

Current behavior:

- Rubric: `site/backend/data/rubric.json` (pass/fail scale, **8 criteria**). Grader model
  comes from `agents.json` `evaluation.model` (currently `gpt-5.6-luna`), overridable via env
  `GRADER_MODEL_OVERRIDE`. Reasoning effort is **per-criterion** (rubric.json per-criterion
  `reasoning`): `physics_correctness` + `distractor_structure_preservation` = high,
  `preserve_physical_content` + `solvable_and_specified` = medium, the rest inherit `low`.
- Each question-level criterion is scored as 0/1 (or -1 for N/A on criteria
  with `na_allowed: true`).
- Reproducibility: every grader is seeded with `GRADER_SEED = 7` (surfaced in the report
  as `grader_seed`).
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
κ-gate (`--labels` / `--kappa-floor`), an experiment ledger
(`site/backend/runtime/meta_eval_ledger.jsonl`), and a versioned calibration format
(`site/backend/data/calibration/*.jsonl`). It **never auto-applies** changes. It is
wrapped by the `.agents/grader-review/SKILL.md` skill (given grader outputs → recommends
prompt/schema tweaks).

Per-run artifacts under `site/backend/outputs/<timestamp>/`:

- `final_result.json`, `agent_outputs/agent_metadata_output.json`
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

- `POST /api/create-data/start`
- `GET /api/create-data/{batch_id}/status`
- `GET /api/create-data/{batch_id}/result`

Concurrency controlled by `BATCH_WORKFLOW_CONCURRENCY` (default 2).
Outputs are nested workflow output folders under a timestamped batch directory.

## Important Files To Edit For Common Tasks

| Task | Files |
|---|---|
| Change models / reasoning / tool usage / themes / per-agent `maxTurns` | `site/backend/data/agents.json` |
| Add or rename an agent | `site/backend/data/agents.json` → `site/backend/src/registry.py` (new accessor if needed) |
| Change guardrail config / behavior | `site/backend/data/agents.json` (`_config.guardrails`), `site/backend/src/workflow/guardrails.py`, `site/backend/data/prompts/{input,content}_guardrail.txt` |
| Change container packages or domains | `site/backend/data/agents.json` (`setup.containerPythonPackages`, `setup.allowedDomains`) |
| Add a registry constant Python needs | `site/backend/src/registry.py` |
| Change agent behavior / instructions | `site/backend/data/prompts/*.txt` |
| Workflow orchestration | `site/backend/src/workflow/engine.py`, `site/backend/src/workflow/payloads.py`, `site/backend/src/main.py` |
| API routes / server state | `site/backend/src/api/**`, `site/backend/src/server.py` |
| Deterministic pipeline validation | `site/backend/src/pipeline_validation.py` |
| Eval scoring / reporting (seed, per-criterion reasoning, failure tags) | `site/backend/src/evaluation.py`, `site/backend/data/rubric.json` |
| Grader self-improvement proposals | `site/backend/src/tools/meta_evaluator.py`, `.agents/grader-review/SKILL.md` |
| Judge ↔ professor agreement | `site/backend/src/tools/judge_agreement.py`, `site/backend/src/tools/professor_comparison.py`, `site/backend/data/professor_comparison/` |
| Frontend UX / state | `site/frontend/src/App.jsx`, `site/frontend/src/state/reducer.js`, `site/frontend/src/state/transcript.js`, `site/frontend/src/hooks/**`, `site/frontend/src/components/**` |
| Chat surface / process rail | `site/frontend/src/sections/ChatConversation.jsx`, `site/frontend/src/components/ui/Chat.jsx` (Avatar/ChatMessage/ChatTranscript/TypingIndicator/Composer), `site/frontend/src/components/ui/Stepper.jsx` (incl. the cycling thinking words + auto-scroll); compose this shared surface in both `App.jsx` and protected `sections/DeveloperMode.jsx` |
| Protected developer tools | `site/frontend/src/sections/DeveloperMode.jsx`, `site/frontend/src/components/DeveloperToolsPanel.jsx`, and the matching developer modals/hooks |
| Frontend styling / design system | `site/frontend/DESIGN.md`, `site/frontend/src/styles/app.css` |
| Add/extend a UI primitive (template) | `site/frontend/src/components/ui/*` + `ui/index.js`, then `site/frontend/src/design-system/sections/Components.jsx` + `ui/ui.test.jsx` + `site/frontend/DESIGN.md` §14 |
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

- The Pi updater deploys only fetched `origin/main` commits. Do not make
  repository edits on the Pi and expect them to be deployed; push reviewed work
  to `main` instead.
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
  The systemd unit reads the release `.env` then this persistent override and
  forcibly sets `ADMIN_LOCAL_DEV_MODE=false`; configure Cloudflare Access and
  Google separately from the tunnel installer.

## Agent Warnings / Gotchas

- Use `site/backend/src/...` paths — not historical `src/...`.
- There is no root `package.json`; root `npm start`/`npm run dev` from legacy
  docs are stale. `site/frontend/package.json` still contains delegating scripts
  that target missing root scripts.
- `site/frontend/DESIGN.md` is the real frontend doc (the design system); there is no
  `site/frontend/README.md`.
- Canonical spelling is `extracter` everywhere. There are **no aliases** in
  `agents.json` — callers must use the exact key name.
- All 10 nodes appear in the frontend pipeline (including `setup`, `output`, and the
  two `kind: "guardrail"` nodes); the list is driven by `useAgentRegistry()`, not hardcoded.
  Guardrail nodes are display/animation-only — they're excluded from the runnable/stoppable/LLM
  sets (`input_to_agent_sequence()`/`llm_agents()`/`stoppable_agents()` skip `kind == "guardrail"`).
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
  in-flight runs do not resume across a restart.
- Model ids are intentional and live in `agents.json`. The pipeline runs a **two-tier
  policy**: the **Generator** uses `gpt-5.6-terra` (high reasoning); **every other LLM
  stage and the grader** use `gpt-5.6-luna`. The runtime nodes (`setup`, `output`) and
  the two guardrail nodes also carry `gpt-5.6-luna` for consistency. These are the
  latest GPT tier the project standardizes on — do not "fix" them back to an older id.
- The stable pre-overhaul baseline is tagged `stable-20260629`.
- `registry._load_raw` is `@lru_cache`. In tests that swap `AGENTS_CONFIG_PATH`,
  call `registry._load_raw.cache_clear()` first or the old data is returned.
  Editing `agents.json` requires a backend restart for the same reason.
- The frontend caches `/api/agents` at module level in `useAgentRegistry.js`
  (reload the page after agents.json changes). Vitest tests that render
  registry-driven components must stub `fetch` and `await loadAgentRegistry()`
  before rendering — see `AgentPipeline.test.jsx` for the pattern.

## Hard Don'ts

- Don't import from `archive/` — nothing references it; it's the user's storage.
- Don't add "remove this later" comments or feature flags for code you can just
  remove now.
- Don't reintroduce the old `latex_syntax_quality` rubric criterion — it was
  removed intentionally.
- Don't mock the database or external state in `pipeline_validation` tests;
  integration tests must exercise the real validator.
- Don't hardcode agent key strings (`"normalizer"`, `"extracter"`, etc.) as Python
  literals — import from `backend.src.registry` instead.
- Don't hardcode pipeline keys, labels, or routing maps in JS — derive from
  `useAgentRegistry()` / `getDerivedRegistry()`.
- Don't add aliases back to `agents.json` — use canonical key names everywhere.
- Don't store agent constants (sequences, names, model ids, theme lists) in
  Python or JS files — they belong in `agents.json` and `registry.py`.

## Visual Changes, Mobile Review, And Cleanup

Every frontend visual change must be verified in the real rendered site. This
applies equally to desktop Codex, Codex Mobile, remote sessions, and similar
harnesses.

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
  shared `ChatConversation` change can affect both.

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
- Keep temporary screenshots only until they have been attached and inspected,
  then remove them unless the user explicitly asks to retain downloadable
  copies.
