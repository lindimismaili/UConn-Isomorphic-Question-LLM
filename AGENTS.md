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
- `setup`, `evaluation`, and `output` are orchestrated by application code in
  `backend/src/server.py` and `backend/src/main.py`.
- The frontend displays a 6-stage visible pipeline ending in `evaluation`.

## Source-Of-Truth Paths

Backend:

- `backend/src/server.py` — FastAPI app and API routes
- `backend/src/run.py` — Uvicorn launcher; auto-rebuilds `dist/` when stale
- `backend/src/main.py` — CLI workflow orchestration
- `backend/src/container.py` — shared OpenAI container bootstrap + LaTeX compile
- `backend/src/evaluation.py` — OpenAI Evals integration; LaTeX `.log` scanning;
  `na_count`/`coverage_mismatch` reporting
- `backend/src/pipeline_validation.py` — deterministic generator/verifier
  contract checks, incl. `validate_distractor_logic_preservation`
- `backend/src/judge_agreement.py` — judge ↔ professor κ / agreement metrics
- `backend/src/schemas.py`, `backend/src/helpers.py`, `backend/src/safety.py`
- `backend/data/agents.json` — agent + model + reasoning config
- `backend/data/prompts/*.txt` — agent instructions
- `backend/data/rubric.json` — eval rubric (pass/fail scale, grader = gpt-5.5)
- `backend/data/skills/**`, `backend/data/evaluation/**`

Frontend:

- `frontend/src/App.jsx` (state via `useReducer`)
- `frontend/src/hooks/useWorkflow.js` (status polling)
- `frontend/src/hooks/useAgents.js` (agent config; normalizes `extractor → extracter`)
- `frontend/src/styles/app.css`

Tooling / analysis:

- `Other/createXLSX.py` — bundles per-batch eval CSVs into `compiled.xlsx`
- `Other/compare_to_professor.py` — CLI: judge ↔ professor κ, confusion,
  disagreements; appends an `Agreement` sheet to `compiled.xlsx`
- `Other/Evaluation.xlsx` — professor's hand-graded ground-truth grid
- `docs/ANALYSIS.md` — full pipeline audit + overhaul notes
- `pyproject.toml`, `.github/workflows/ci.yml` — ruff + pytest CI

Ignore the top-level `src/` directory; it only contains Python cache artifacts.

## Commands That Work

One-time setup:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt
npm -C frontend install
```

Full app locally (frontend served from `dist/`):

```bash
.venv/bin/python -m backend.src.run --host 127.0.0.1 --port 8000
```

Available at `http://127.0.0.1:8000`.

Frontend-only Vite dev server (proxies `/api`, `/version.json`, `/data`, `/skills`
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
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
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
already in the environment.

Required for real workflow execution:

- `OPENAI_API_KEY`

Optional app/runtime vars:

- `MAX_UPLOAD_BYTES`
- `WORKFLOW_TTL_SECONDS`
- `WORKFLOW_MAX_ENTRIES`
- `OPENAI_STATUS_TIMEOUT_SECONDS`
- `BATCH_WORKFLOW_CONCURRENCY` (default 2)
- `OPENAI_CONTAINER_BOOTSTRAP_MODEL`
- `OPENAI_CONTAINER_ALLOWED_DOMAINS`

Deploy-related vars also present in `.env` (not required for local startup):
`GITHUB_SSH_KEY_PATH`, `GITHUB_SSH_KEY_PASSPHRASE`, `GIT_FORCE_BRANCH`.

## Architecture

### Backend

- FastAPI in `backend/src/server.py`; uvicorn launcher in `backend/src/run.py`.
- In-memory `workflows` and `batch_jobs` dicts; no database. Pruned by TTL +
  max entry count; not durable across restarts.
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

Runtime-managed (`backend/src/main.py` / `server.py`):

6. `evaluation` — runs OpenAI Evals; writes the report + spreadsheet + rubric snapshot
7. `output` — writes `final_result.json`, `agent_outputs.json`, file metadata

## API Surface

Routes in `backend/src/server.py`:

- `GET /api/health`, `GET /api/openai/status`, `GET /version.json`
- `POST /api/upload` (5 files max, 20 MB each by default; `.pdf .docx .doc .txt`)
- `GET /api/sliders`, `GET /api/agents`
- `GET /api/prompts/{prompt_name}`
- `GET /api/skills`, `GET /api/skills/{skill_name}`
- `GET /api/evaluation/files`
- `POST /api/workflow/start`
- `GET /api/workflow/{workflow_id}/status`
- `GET /api/workflow/{workflow_id}/result`
- `GET /api/workflow/{workflow_id}/stream` (SSE; current frontend polls instead)
- `GET /api/download/{workflow_id}/{filename}`
- `POST /api/create-data/start`
- `GET /api/create-data/{batch_id}/status`
- `GET /api/create-data/{batch_id}/result`

## Evaluation System

Lives in `backend/src/evaluation.py`. See `docs/ANALYSIS.md` for the full audit.

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

- `final_result.json`, `agent_outputs.json`
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
| Change agent behavior / instructions | `backend/data/prompts/*.txt`, `backend/data/skills/**` |
| Workflow orchestration / API | `backend/src/server.py`, `backend/src/main.py`, `backend/src/helpers.py`, `backend/src/schemas.py` |
| Deterministic pipeline validation | `backend/src/pipeline_validation.py` |
| Eval scoring / reporting | `backend/src/evaluation.py`, `backend/data/rubric.json` |
| Judge ↔ professor agreement | `backend/src/judge_agreement.py`, `Other/compare_to_professor.py` |
| Frontend UX / state | `frontend/src/App.jsx`, `frontend/src/hooks/**`, `frontend/src/components/**`, `frontend/src/styles/app.css` |

## Deployment Notes

Deploy scripts target Raspberry Pi + nginx + systemd: `deploy/raspi-deploy.sh`,
`docs/DEPLOYMENT_RPI_NGINX.md`, `docs/LIVE.md`. Treat them as operational
guides, not source of truth for local development.

## Agent Warnings / Gotchas

- Use `backend/src/...` paths — not historical `src/...`.
- There is no root `package.json`; root `npm start`/`npm run dev` from legacy
  docs are stale. `frontend/package.json` still contains delegating scripts
  that target missing root scripts.
- `frontend/README.md` is the stock Vite template, not project docs.
- Canonical spelling is `extracter` everywhere.
- Frontend pipeline ends at `evaluation`; backend also tracks `output`.
- `dist/` is generated; `backend/src/run.py` rebuilds it when stale.
- Workflow state is in-memory only — server restart drops active runs.
- No ORM, no database.
- Model id `gpt-5.5` is intentional (kept across `agents.json` and
  `rubric.json`); do not "fix" it.

## Local Visual Debugging

```bash
npx playwright install chromium
npx playwright screenshot --browser=chromium --full-page http://127.0.0.1:8000 /tmp/app.png
```
