# UConn Isomorphic Question Generator — AGENTS.md

Onboarding guide for coding agents (Claude, Codex, etc.) working in this repo.
**This file is the single source of truth for the repo** — `CLAUDE.md` just points
here. Older `.claude/CLAUDE.md` / `.codex/AGENTS.md` references are stale.

## Repository Docs

Every Markdown doc tracked in the repo:

- [README.md](README.md) — human-facing setup, environment, run/CLI/output reference
- [CLAUDE.md](CLAUDE.md) — Claude Code entry point; a pointer straight to this file
- [site/backend/BACKEND.md](site/backend/BACKEND.md) — backend reference: what every file does,
  key functions, the 8-stage workflow, the HTTP API routes, `data/` files, and tests
- [site/frontend/DESIGN.md](site/frontend/DESIGN.md) — frontend design system: tokens, layout,
  buttons, modals, themes, transitions, consistency rules (**read before any UI change**)
- [deploy/DEPLOY.md](deploy/DEPLOY.md) — deployment + local preview scripts (operational, not source of truth)

## What This Repo Is

FastAPI + React app that generates isomorphic physics multiple-choice questions
from uploaded source documents using the OpenAI Agents SDK.

Full runtime pipeline (8 stages):

`setup → normalizer → extracter → verifier → generator → latexifier → evaluation → output`

- `site/backend/data/agents.json` is the **single source of truth** for all 8 agents plus
  global constants (`_config`). Python reads through `site/backend/src/registry.py`;
  the frontend derives from `GET /api/agents` via `site/frontend/src/hooks/useAgentRegistry.js`.
- `setup` and `output` are `kind: "runtime"` nodes; the other six are `kind: "agent"` LLM calls.
- Both the FastAPI server and the CLI call the same workflow engine.

## Source-Of-Truth Paths

Backend:

- `site/backend/src/server.py` — FastAPI app factory (CORS, lifespan, static mount).
  Launch with `uvicorn backend.src.server:app` or `python -m backend.src.server`.
- `site/backend/src/api/` — route handlers split by domain (`health`, `config`,
  `files`, `workflows`, `batch`) plus `state.py` (in-memory registries, config,
  helpers, workflow-runner wrappers, background pruning loop)
- `site/backend/src/workflow/` — shared engine, agent factory, payload builders,
  resume/replay helpers, SQLite `store.py` (single source of truth for orchestration)
- `site/backend/src/main.py` — CLI workflow orchestration (delegates to the engine)
- `site/backend/src/container.py` — shared OpenAI container bootstrap + LaTeX compile
- `site/backend/src/evaluation.py` — OpenAI Evals integration;
  `na_count`/`coverage_mismatch` reporting
- `site/backend/src/pipeline_validation.py` — deterministic generator/verifier
  contract checks, incl. `validate_distractor_logic_preservation`
- `site/backend/src/tools/judge_agreement.py` — judge ↔ professor κ / agreement metrics
- `site/backend/src/registry.py` — **lazy-loaded gateway** for all agent constants
  (`@lru_cache`); import from here instead of hardcoding names anywhere in Python
- `site/backend/src/schemas.py`, `site/backend/src/helpers.py`, `site/backend/src/safety.py`,
  `site/backend/src/paths.py`
- `site/backend/data/agents.json` — single source of truth for all 8 agents; every entry
  carries `sequence`, `name`, `description`, `model`, `reasoning`, `kind`, `isLLM`,
  `hasPrompt`, `acceptsSourceFiles`, `isStoppable`, `useCodeInterpreter`,
  `countLabel`, `countField` (output array used for question counts), `aliases`,
  `inputFrom`/`outputTo` (flow display names), `cliDescription`, `resultField`;
  the evaluation entry adds `promptName`; the setup entry adds `allowedDomains` +
  `containerPythonPackages`; `_config` holds `themes`, `apiTitle`, `apiDescription`
- `site/backend/data/prompts/*.txt` — agent prompt instructions; `bootstrap.txt` and
  `bootstrapCompile.txt` are container shell-task templates with `{var}` placeholders
- `site/backend/data/rubric.json` — eval rubric criteria only (pass/fail scale, 9 criteria);
  grader model comes from `agents.json` `evaluation.model`
- `site/backend/data/evaluation/**` — bundled source PDFs for the Create Data batch flow

Frontend:

- `site/frontend/DESIGN.md` — UI element mapping (design tokens, button/modal system,
  consistency rules). **Source of truth before adding any UI; reuse, don't invent.**
- `site/frontend/src/App.jsx` (state via `useReducer`)
- `site/frontend/src/hooks/useAgentRegistry.js` — **module-level singleton** that fetches
  `/api/agents` once; exports `useAgentRegistry()` hook (React) and `getDerivedRegistry()`
  sync accessor (for reducers). All pipeline keys, labels, and routing derive from this.
- `site/frontend/src/hooks/useWorkflow.js` (status polling), `useAgents.js` (triggers registry
  load), `useCreateData.js`, `useFileUpload.js`
- `site/frontend/src/utils/` — shared `api.js` (HTTP client), `abortableSleep.js`,
  `ids.js`, `files.js` (file `getViewerType` dispatch + download URL helpers)
- `site/frontend/src/components/modals/` (one `Modal.jsx` shell; `eval/` holds the
  split Evaluator-results pieces; `useFileViewer.jsx` wires the generic file viewers)
- `site/frontend/src/styles/app.css` (design tokens in `:root`)

Tooling / analysis:

- `Other/createXLSX.py` — bundles per-batch eval CSVs into `compiled.xlsx`
- `Other/compare_to_professor.py` — CLI: judge ↔ professor κ, confusion,
  disagreements; appends an `Agreement` sheet to `compiled.xlsx`
- `Other/Evaluation.xlsx` — professor's hand-graded ground-truth grid
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

Judge ↔ professor agreement on a generated batch:

```bash
python Other/compare_to_professor.py Other/<timestamp>-other-phase1-eval-batch
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
committed key); `.env.example` documents the full variable list. Local-only
overrides go in `.env.local` / `.env.*.local`, which stay gitignored.

Required for real workflow execution:

- `OPENAI_API_KEY`

Optional app/runtime vars (see `.env.example` for the full list + defaults):

- `MAX_UPLOAD_BYTES`
- `WORKFLOW_TTL_SECONDS`, `WORKFLOW_MAX_ENTRIES`
- `PRUNE_INTERVAL_SECONDS` (background prune cadence, default 60)
- `OPENAI_STATUS_TIMEOUT_SECONDS`
- `BATCH_WORKFLOW_CONCURRENCY` (default 2)
- `OPENAI_CONTAINER_ALLOWED_DOMAINS` — overrides `setup.allowedDomains` from `agents.json`
- `GENERATION_STRATEGY` — generator strategy: `holistic` (default) or `decompose`
  (decompose-first; uses `prompts/generator_decompose.txt` and fills each generated
  question's `decomposition` field). See `workflow/agents.py::generation_strategy`.
- `ENABLE_STAGE_CACHE` — test feature, default `false`. When truthy, reuses a prior
  stage's output (extracter, generator) keyed on the stage input + model + reasoning +
  prompt, persisted in `site/backend/runtime/stage_cache.sqlite3`. See
  `workflow/stage_cache.py`.

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
  (currently `medium` for Verifier/Generator/Latexifier).

### Frontend

- React 19 + Vite 7; centralized `useReducer` state in `App.jsx`.
- Progress comes from **polling** `GET /api/workflow/{id}/status` (see
  `useWorkflow.js`); the SSE `/stream` endpoint is unused.
- All HTTP goes through `src/utils/api.js` (`requestJson`, `getJsonOrNull`,
  `fetchWithAbort`) — don't hand-roll `fetch` + error handling in hooks.
- Styling follows `site/frontend/DESIGN.md`: design tokens in `:root`, one `.btn`
  variant system, one `Modal.jsx` shell. Reuse variants and tokens; never add a
  one-off `*-btn` class or hardcode color/size/motion. Buttons in the same group
  share one height tier.
- **Compose from components, not raw elements (templates-first).** Feature code
  builds UI only from the reusable primitives in `site/frontend/src/components/ui/`
  (Button, IconButton, Card, TextInput/TextArea/Select, ToggleSwitch, Badge,
  StatusChip/ScoreChip/Pill, StatusDot, LogItem/LogList, EmptyState, Tabs,
  DataTable/KeyValueTable, Modal) — not from raw `<button className="btn …">` /
  `<input>` / ad-hoc class strings. Primitives are thin wrappers over the
  documented classes (no new CSS). If a genuinely new element is needed, add it
  as a `ui/` primitive (+ a showcase specimen + a `ui/ui.test.jsx` assertion)
  **first**, then use it. See `site/frontend/DESIGN.md` §14.
- **Design-system showcase** lives at `site/frontend/design-system.html` (a second
  Vite entry; `npm -C site/frontend run dev` → `/design-system.html`). It renders the
  real `ui/` primitives, organized Foundations → Components → Patterns, and is
  the living catalog to review building blocks before composing the site.
- Canonical spelling is `extracter` everywhere. No aliases exist in `agents.json`;
  callers must use canonical key names.
- All pipeline keys, labels, and routing come from `useAgentRegistry()` —
  never hardcode agent key strings in JS files.
- The frontend renders all 8 pipeline nodes (including `setup` and `output`)
  because `AgentPipeline` derives its key list from the registry.
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
- **Frontend follows `site/frontend/DESIGN.md`** (see the Frontend notes above).

### Pipeline parallelism

- Within one workflow the 8 stages are sequential (each consumes the previous output).
- Multi-file uploads in one workflow are intentionally combined into one dataset
  (the extracter prompt handles this).
- The batch endpoint runs workflows concurrently with `Semaphore(BATCH_WORKFLOW_CONCURRENCY=2)`.
- `Other/runEvals.py` parallelizes per-PDF evaluations with `--concurrency` (default 2).

## Workflow Stages

All 8 stages are defined in `site/backend/data/agents.json` and driven by the engine:

0. `setup` *(runtime)* — creates the shared OpenAI container; bootstraps LaTeX + Python deps
1. `normalizer` — parses the user prompt into structured config
2. `extracter` — reads uploaded files and extracts source questions
3. `verifier` — validates answers, fills in explanations / `distractor_logic`
4. `generator` — creates isomorphic questions
5. `latexifier` — produces LaTeX and triggers PDF compilation in the container
6. `evaluation` — runs OpenAI Evals; writes the report + spreadsheet + rubric snapshot
7. `output` *(runtime)* — writes `final_result.json`, `agent_outputs/` artifacts, file metadata

## API Surface

Routes live in `site/backend/src/api/` (registered on the app in `server.py`):

- `GET /api/health`, `GET /api/openai/status`, `GET /version.json`
- `POST /api/upload` (5 files max, 20 MB each by default; documents, images,
  text/markdown, and LaTeX — see `ALLOWED_UPLOAD_EXTENSIONS` in `api/state.py`)
- `GET /api/dropdown`, `GET /api/rubric`, `GET /api/agents`
- `GET /api/prompts/{prompt_name}`
- `GET /api/evaluation/files`
- `POST /api/workflow/start`
- `POST /api/workflow/rerun-from-agent`, `POST /api/workflow/start-from-agent-input`
- `GET /api/workflow/{workflow_id}/status`
- `GET /api/workflow/{workflow_id}/result`
- `GET /api/workflow/{workflow_id}/stream` (SSE; current frontend polls instead)
- `GET /api/download/{workflow_id}/{file_path}`
- `GET /api/download-zip/{workflow_id}` (zips the whole output folder once,
  reuses the archive on later requests)
- `POST /api/create-data/start`, `POST /api/create-data/{batch_id}/stop`
- `GET /api/create-data/{batch_id}/status`
- `GET /api/create-data/{batch_id}/result`

## Evaluation System

Lives in `site/backend/src/evaluation.py`.

Current behavior:

- Rubric: `site/backend/data/rubric.json` (pass/fail scale, 9 criteria). Grader model
  comes from `agents.json` `evaluation.model` (currently `gpt-5.5`); reasoning
  effort from rubric `"reasoning"` field (currently `"low"`).
- Each question-level criterion is scored as 0/1 (or -1 for N/A on criteria
  with `na_allowed: true`).
- LaTeX criterion is deterministic: checks artifact existence AND scans the
  `.log` for fatal markers (`! LaTeX Error`, `Undefined control sequence`,
  `Emergency stop`, etc.).
- Aggregation reports `na_count` and `na_rate` per criterion; a
  `coverage_mismatch` is recorded under `deterministic_checks` when the eval
  run returns fewer items than were submitted.
- `validate_distractor_logic_preservation` (pipeline_validation.py) is a
  deterministic pre-judge check that every source `distractor_logic` label
  appears in the generated option set.

Per-run artifacts under `site/backend/outputs/<timestamp>/`:

- `final_result.json`, `agent_outputs/agent_metadata_output.json`
- `agent_outputs/<agent>/agent_input.jsonl` + `agent_output.jsonl` (per attempt)
- `isomorphic_questions.tex`, `isomorphic_questions.pdf`
- `evaluation_outputs/evaluation_report.json`
- `evaluation_outputs/evaluation_dataset.jsonl`
- `evaluation_outputs/evaluation_agents.jsonl` (raw judge results)
- `evaluation_outputs/evaluation_spreadsheet.csv`
- `evaluation_outputs/rubric.json` (snapshot)

Per-batch `Other/<timestamp>-other-phase1-eval-batch/`:

- `batch_summary.json` at the batch root
- `XLSX/compiled.xlsx` (`Summary` + per-PDF sheets; after running
  `compare_to_professor.py`, also a `judge_agreement_report.json`,
  `judge_disagreements.csv`, and an `Agreement` sheet in `compiled.xlsx`)

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
| Change models / reasoning / tool usage / themes | `site/backend/data/agents.json` |
| Add or rename an agent | `site/backend/data/agents.json` → `site/backend/src/registry.py` (new accessor if needed) |
| Change container packages or domains | `site/backend/data/agents.json` (`setup.containerPythonPackages`, `setup.allowedDomains`) |
| Add a registry constant Python needs | `site/backend/src/registry.py` |
| Change agent behavior / instructions | `site/backend/data/prompts/*.txt` |
| Workflow orchestration | `site/backend/src/workflow/engine.py`, `site/backend/src/workflow/payloads.py`, `site/backend/src/main.py` |
| API routes / server state | `site/backend/src/api/**`, `site/backend/src/server.py` |
| Deterministic pipeline validation | `site/backend/src/pipeline_validation.py` |
| Eval scoring / reporting | `site/backend/src/evaluation.py`, `site/backend/data/rubric.json` |
| Judge ↔ professor agreement | `site/backend/src/tools/judge_agreement.py`, `Other/compare_to_professor.py` |
| Frontend UX / state | `site/frontend/src/App.jsx`, `site/frontend/src/hooks/**`, `site/frontend/src/components/**` |
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
`preview.sh` for a local preview build, `DEPLOY.md` for notes). Treat them as
operational guides, not source of truth for local development.

## Agent Warnings / Gotchas

- Use `site/backend/src/...` paths — not historical `src/...`.
- There is no root `package.json`; root `npm start`/`npm run dev` from legacy
  docs are stale. `site/frontend/package.json` still contains delegating scripts
  that target missing root scripts.
- `site/frontend/DESIGN.md` is the real frontend doc (the design system); there is no
  `site/frontend/README.md`.
- Canonical spelling is `extracter` everywhere. There are **no aliases** in
  `agents.json` — callers must use the exact key name.
- All 8 stages appear in the frontend pipeline (including `setup` and `output`);
  the list is driven by `useAgentRegistry()`, not hardcoded.
- `dist/` is generated by `npm -C site/frontend run build`; it is gitignored.
- Workflow state is in-memory plus a SQLite mirror (`site/backend/runtime/`); active
  in-flight runs do not resume across a restart.
- Model id `gpt-5.5` is intentional and lives in `agents.json`; every agent
  entry including `setup` and `output` carries it. Do not "fix" it.
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

## Local Visual Debugging

Quick one-shot screenshot of the built app:

```bash
npx playwright install chromium
npx playwright screenshot --browser=chromium --full-page http://127.0.0.1:8000 /tmp/app.png
```

For iterating on CSS/layout, run the Vite dev server (`npm -C site/frontend run dev:ui`)
and drive it with the Playwright MCP — `browser_resize` to test the §3 breakpoints
and `browser_evaluate` with `getBoundingClientRect()`/`getComputedStyle()` to
*measure* element heights rather than eyeballing screenshots (how the dev-tools
log-clipping and prompt-row-wrapping bugs were pinned down). Notes:

- The dev server needs the **backend up on `:8000`** for `/api/agents`,
  `/api/dropdown`, etc.; without it the agent pipeline stays on its empty
  placeholder (the top panels and dev-tools still render for layout work).
- Vite auto-picks the next free port if `5173` is taken (another local project may
  hold it). To verify a hover-only style without a live run, inject a throwaway
  `.agent-card` into the DOM via `browser_evaluate`, hover it, and read the
  computed `transform`/`border-color`.
- Put screenshots in a workspace `tmp/` and delete them when done.
