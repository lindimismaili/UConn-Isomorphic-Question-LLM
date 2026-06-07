# UConn Isomorphic Question Generator — AGENTS.md

Onboarding guide for coding agents (Claude, Codex, etc.) working in this repo.
This file is the single source of truth; older `.claude/CLAUDE.md` / `.codex/AGENTS.md`
references are stale.

## What This Repo Is

FastAPI + React app that generates isomorphic physics multiple-choice questions
from uploaded source documents using the OpenAI Agents SDK.

User-facing pipeline:

`Normalizer -> Extracter -> Verifier -> Generator -> LaTeX Formatter`

Actual runtime pipeline:

`setup -> normalizer -> extracter -> verifier -> generator -> latex -> evaluation -> output`

Distinction:

- `backend/data/agents.json` defines only the 5 OpenAI agents.
- `setup`, `evaluation`, and `output` are orchestrated by the shared workflow
  engine (`backend/src/workflow/engine.py`), which both the server and CLI call.
- The frontend displays a 6-stage visible pipeline ending in `evaluation`.

## Source-Of-Truth Paths

Backend:

- `backend/src/server.py` — FastAPI app factory (CORS, lifespan, static mount).
  Launch with `uvicorn backend.src.server:app` or `python -m backend.src.server`.
- `backend/src/api/` — route handlers split by domain (`health`, `config`,
  `files`, `workflows`, `batch`) plus `state.py` (in-memory registries, config,
  helpers, workflow-runner wrappers, background pruning loop)
- `backend/src/workflow/` — shared engine, agent factory, payload builders,
  resume/replay helpers, SQLite `store.py` (single source of truth for orchestration)
- `backend/src/main.py` — CLI workflow orchestration (delegates to the engine)
- `backend/src/container.py` — shared OpenAI container bootstrap + LaTeX compile
- `backend/src/evaluation.py` — OpenAI Evals integration;
  `na_count`/`coverage_mismatch` reporting
- `backend/src/pipeline_validation.py` — deterministic generator/verifier
  contract checks, incl. `validate_distractor_logic_preservation`
- `backend/src/tools/judge_agreement.py` — judge ↔ professor κ / agreement metrics
- `backend/src/schemas.py`, `backend/src/helpers.py`, `backend/src/safety.py`,
  `backend/src/paths.py`
- `backend/data/agents.json` — agent + model + reasoning config
- `backend/data/prompts/*.txt` — agent instructions
- `backend/data/rubric.json` — eval rubric (pass/fail scale, grader = gpt-5.5)
- `backend/data/evaluation/**` — bundled source PDFs for the Create Data batch flow

Frontend:

- `frontend/DESIGN.md` — UI element mapping (design tokens, button/modal system,
  consistency rules). **Source of truth before adding any UI; reuse, don't invent.**
- `frontend/src/App.jsx` (state via `useReducer`)
- `frontend/src/hooks/useWorkflow.js` (status polling), `useAgents.js`
  (agent config; normalizes `extractor → extracter`), `useCreateData.js`, `useFileUpload.js`
- `frontend/src/utils/` — shared `api.js` (HTTP client), `abortableSleep.js`, `ids.js`
- `frontend/src/components/modals/` (one `Modal.jsx` shell; `eval/` holds the
  split Evaluator-results pieces)
- `frontend/src/styles/app.css` (design tokens in `:root`)

Tooling / analysis:

- `Other/createXLSX.py` — bundles per-batch eval CSVs into `compiled.xlsx`
- `Other/compare_to_professor.py` — CLI: judge ↔ professor κ, confusion,
  disagreements; appends an `Agreement` sheet to `compiled.xlsx`
- `Other/Evaluation.xlsx` — professor's hand-graded ground-truth grid
- `pyproject.toml` — ruff + pytest config (line length 110, target py310)

## Commands That Work

One-time setup:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt
npm -C frontend install
```

Full app locally (frontend served from `dist/`; build it first with
`npm -C frontend run build`):

```bash
.venv/bin/python -m uvicorn backend.src.server:app --host 127.0.0.1 --port 8000
```

Available at `http://127.0.0.1:8000`.

Frontend-only Vite dev server (proxies `/api`, `/version.json`, `/data`
to `http://127.0.0.1:8000`; override with `VITE_BACKEND_URL`):

```bash
npm -C frontend run dev:ui
```

CLI workflow:

```bash
.venv/bin/python -m backend.src.main "archive/Question Data Files/PDF/Quiz_Compilation.pdf" -n 2 -i "Make questions harder"
```

Tests + lint:

```bash
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

## Environment Variables

`.env` at the repo root is loaded manually by backend code if values aren't
already in the environment. `.env` is gitignored; copy `.env.example` to `.env`
and fill it in. Never commit real keys.

Required for real workflow execution:

- `OPENAI_API_KEY`

Optional app/runtime vars (see `.env.example` for the full list + defaults):

- `MAX_UPLOAD_BYTES`
- `WORKFLOW_TTL_SECONDS`, `WORKFLOW_MAX_ENTRIES`
- `PRUNE_INTERVAL_SECONDS` (background prune cadence, default 60)
- `OPENAI_STATUS_TIMEOUT_SECONDS`
- `BATCH_WORKFLOW_CONCURRENCY` (default 2)
- `OPENAI_CONTAINER_BOOTSTRAP_MODEL`, `OPENAI_CONTAINER_ALLOWED_DOMAINS`

## Architecture

### Backend

- FastAPI app factory in `backend/src/server.py`; routers under `backend/src/api/`.
  Launch with `uvicorn backend.src.server:app`.
- In-memory `workflows` and `batch_jobs` dicts in `backend/src/api/state.py`,
  mirrored to a SQLite store (`backend/src/workflow/store.py`). Pruned by TTL +
  max entry count on a background interval (not on the request path).
- Uploads → `backend/uploads/<sessionId>/`.
- Run outputs → `backend/outputs/<timestamp>/`.
- If `dist/index.html` is missing, falls back to a simple HTML message.

### OpenAI agent runtime

- One shared Code Interpreter container per workflow; reused across agent calls.
- Container bootstrap installs LaTeX + PDF tooling (see `backend/src/container.py`).
- Outbound domain allowlist defaults to PyPI + Linux mirrors;
  override via `OPENAI_CONTAINER_ALLOWED_DOMAINS`.
- Structured outputs are enforced via Pydantic `output_type=` on each Agent
  (`agents` SDK), not via `response_format`.
- Reasoning effort is set per-agent in `backend/data/agents.json`
  (`Reasoning(effort="medium")` for Verifier/Generator).

### Frontend

- React 19 + Vite 7; centralized `useReducer` state in `App.jsx`.
- Progress comes from **polling** `GET /api/workflow/{id}/status` (see
  `useWorkflow.js`); the SSE `/stream` endpoint is unused.
- All HTTP goes through `src/utils/api.js` (`requestJson`, `getJsonOrNull`,
  `fetchWithAbort`) — don't hand-roll `fetch` + error handling in hooks.
- Styling follows `frontend/DESIGN.md`: design tokens in `:root`, one `.btn`
  variant system, one `Modal.jsx` shell. Reuse variants and tokens; never add a
  one-off `*-btn` class or hardcode color/size/motion. Buttons in the same group
  share one height tier.
- Canonical spelling is `extracter` everywhere; `useAgents.js` normalizes
  `extractor → extracter`.
- The frontend currently knows about `evaluation` but not the backend-only
  `output` stage.

## Workflow Stages

Configured agents (`backend/data/agents.json`):

1. `normalizer` — parses the user prompt into structured config
2. `extracter` — reads uploaded files and extracts source questions
3. `verifier` — validates answers, fills in explanations / `distractor_logic`
4. `generator` — creates isomorphic questions
5. `latex` — produces LaTeX and triggers PDF compilation in the container

Runtime-managed by the engine (`backend/src/workflow/engine.py`):

6. `evaluation` — runs OpenAI Evals; writes the report + spreadsheet + rubric snapshot
7. `output` — writes `final_result.json`, `agent_outputs/` artifacts, file metadata

## API Surface

Routes live in `backend/src/api/` (registered on the app in `server.py`):

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
- `POST /api/create-data/start`, `POST /api/create-data/{batch_id}/stop`
- `GET /api/create-data/{batch_id}/status`
- `GET /api/create-data/{batch_id}/result`

## Evaluation System

Lives in `backend/src/evaluation.py`.

Current behavior:

- Rubric: `backend/data/rubric.json` (pass/fail scale, 9 criteria; grader = `gpt-5.5`,
  reasoning effort `low`).
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

Per-run artifacts under `backend/outputs/<timestamp>/`:

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

UI's "Create Data" flow batch-runs over files in `backend/data/evaluation/`.

- `POST /api/create-data/start`
- `GET /api/create-data/{batch_id}/status`
- `GET /api/create-data/{batch_id}/result`

Concurrency controlled by `BATCH_WORKFLOW_CONCURRENCY` (default 2).
Outputs are nested workflow output folders under a timestamped batch directory.

## Important Files To Edit For Common Tasks

| Task | Files |
|---|---|
| Change models / reasoning / tool usage | `backend/data/agents.json` |
| Change agent behavior / instructions | `backend/data/prompts/*.txt` |
| Workflow orchestration | `backend/src/workflow/engine.py`, `backend/src/workflow/payloads.py`, `backend/src/main.py` |
| API routes / server state | `backend/src/api/**`, `backend/src/server.py` |
| Deterministic pipeline validation | `backend/src/pipeline_validation.py` |
| Eval scoring / reporting | `backend/src/evaluation.py`, `backend/data/rubric.json` |
| Judge ↔ professor agreement | `backend/src/tools/judge_agreement.py`, `Other/compare_to_professor.py` |
| Frontend UX / state | `frontend/src/App.jsx`, `frontend/src/hooks/**`, `frontend/src/components/**` |
| Frontend styling / design system | `frontend/DESIGN.md`, `frontend/src/styles/app.css` |

## Deployment Notes

Deploy scripts live in `deploy/` (`deploy.sh` for the systemd/uvicorn service,
`preview.sh` for a local preview build, `DEPLOY.md` for notes). Treat them as
operational guides, not source of truth for local development.

## Agent Warnings / Gotchas

- Use `backend/src/...` paths — not historical `src/...`.
- There is no root `package.json`; root `npm start`/`npm run dev` from legacy
  docs are stale. `frontend/package.json` still contains delegating scripts
  that target missing root scripts.
- `frontend/README.md` is the stock Vite template; `frontend/DESIGN.md` is the
  real frontend doc.
- Canonical spelling is `extracter` everywhere.
- Frontend pipeline ends at `evaluation`; backend also tracks `output`.
- `dist/` is generated by `npm -C frontend run build`; it is gitignored.
- Workflow state is in-memory plus a SQLite mirror (`backend/runtime/`); active
  in-flight runs do not resume across a restart.
- Model id `gpt-5.5` is intentional (kept across `agents.json` and
  `rubric.json`); do not "fix" it.

## Local Visual Debugging

```bash
npx playwright install chromium
npx playwright screenshot --browser=chromium --full-page http://127.0.0.1:8000 /tmp/app.png
```
