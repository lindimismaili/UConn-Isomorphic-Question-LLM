# UConn Isomorphic Question Generator — AGENTS.md

This file is the project onboarding guide for coding agents working in this repo.

## What This Repo Actually Is

This is a FastAPI + React application that generates isomorphic physics multiple-choice questions from uploaded source documents using an OpenAI Agents SDK workflow.

The user-facing idea is still the classic 5-agent pipeline:

`Normalizer -> Extracter -> Verifier -> Generator -> LaTeX Formatter`

But the current running app has additional runtime-managed stages:

- `setup`
- `evaluation`
- `output` on the backend workflow record

Important distinction:

- `backend/data/agents.json` defines only the 5 OpenAI agents.
- `evaluation` and `output` are orchestrated by application code in `backend/src/server.py`.
- The current frontend seeds `setup` and `evaluation` in state and displays a 6-stage visible pipeline ending in `evaluation`.

## Source Of Truth Paths

Use these paths, not the older shortened `src/...` paths from legacy docs:

- Backend API/server: `backend/src/server.py`
- Backend run wrapper: `backend/src/run.py`
- Backend CLI workflow: `backend/src/main.py`
- Shared container bootstrap + LaTeX compile: `backend/src/container.py`
- Evals integration: `backend/src/evaluation.py`
- Schemas: `backend/src/schemas.py`
- Helpers: `backend/src/helpers.py`
- Agent config: `backend/data/agents.json`
- Agent prompts: `backend/data/prompts/*.txt`
- Skills: `backend/data/skills/**`
- Rubric: `backend/data/rubric.json`
- Evaluation demo files: `backend/data/evaluation/**`
- Frontend app root: `frontend/src/App.jsx`
- Workflow polling hook: `frontend/src/hooks/useWorkflow.js`
- Agent config hook: `frontend/src/hooks/useAgents.js`
- Global styles: `frontend/src/styles/app.css`

Ignore the top-level `src/` directory for application work. In this checkout it only contains Python cache artifacts, not the active source tree.

## Repo Reality Check

Several docs/scripts in this repo are stale. Do not assume they are correct without verifying against the files above.

Current known mismatches:

- There is no root `package.json`.
- Root-level `npm start` and `npm run dev` commands described in legacy docs are not valid in this checkout unless the user adds their own wrapper scripts later.
- `frontend/package.json` contains `dev` and `start` scripts that delegate to missing root scripts.
- `frontend/README.md` is still the default Vite template and is not project documentation.
- Older docs describe only the 5-agent pipeline, but the current backend workflow includes evaluation and output handling.
- Older Claude docs reference `src/server.py`, `src/main.py`, etc. The live code is under `backend/src/`.

## Commands That Work

These are the commands that work in the current repo state.

### One-time setup

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt
npm -C frontend install
```

Notes:

- Python `3.12` is a safer choice than `3.14` for this repo.
- Frontend dependencies live under `frontend/`, not a root package.

### Run the full site locally

```bash
.venv/bin/python -m backend.src.run --host 127.0.0.1 --port 8000
```

What `backend/src/run.py` does:

- ensures `backend/uploads/` and `backend/outputs/` exist
- rebuilds `dist/` if frontend inputs are newer
- starts Uvicorn against `backend.src.server:app`

The app is then available at:

- `http://127.0.0.1:8000`
- `http://localhost:8000`

### Frontend-only dev server

Use the direct Vite script instead of the stale delegating script:

```bash
npm -C frontend run dev:ui
```

Vite proxies these paths to the backend by default:

- `/api`
- `/version.json`
- `/data`
- `/skills`

Default proxy target:

- `http://127.0.0.1:8000`

Override with:

- `VITE_BACKEND_URL`

### Run the CLI workflow

```bash
.venv/bin/python -m backend.src.main "archive/Question Data Files/PDF/Quiz_Compilation.pdf" -n 2 -i "Make questions harder"
```

## Environment Variables

The app manually loads `.env` from the repo root if values are not already set in the environment.

Required for real workflow execution:

- `OPENAI_API_KEY`

Important optional app/runtime vars:

- `MAX_UPLOAD_BYTES`
- `WORKFLOW_TTL_SECONDS`
- `WORKFLOW_MAX_ENTRIES`
- `OPENAI_STATUS_TIMEOUT_SECONDS`
- `BATCH_WORKFLOW_CONCURRENCY`
- `OPENAI_CONTAINER_BOOTSTRAP_MODEL`
- `OPENAI_CONTAINER_ALLOWED_DOMAINS`

Current `.env` also contains deploy-related Git variables:

- `GITHUB_SSH_KEY_PATH`
- `GITHUB_SSH_KEY_PASSPHRASE`
- `GIT_FORCE_BRANCH`

Those are not required for local app startup.

## High-Level Architecture

### Backend

Framework and shape:

- FastAPI app in `backend/src/server.py`
- Uvicorn launcher in `backend/src/run.py`
- CLI orchestration in `backend/src/main.py`
- in-memory workflow state; no database

Key behavior:

- uploads are stored under `backend/uploads/<sessionId>/`
- run outputs are stored under `backend/outputs/<timestamp>/`
- frontend build artifacts are served from `dist/`
- if `dist/index.html` is missing, the server falls back to a simple HTML message

Workflow state:

- stored in global `workflows` and `batch_jobs` dictionaries
- pruned by TTL and maximum entry count
- not durable across process restarts

### OpenAI agent runtime

The backend creates a single shared Code Interpreter container per workflow and reuses it across agent calls.

Container/bootstrap details:

- implemented in `backend/src/container.py`
- bootstrap installs LaTeX and PDF tooling inside the hosted container
- allowed outbound domains default to PyPI and Linux package mirrors
- allowlist can be overridden with `OPENAI_CONTAINER_ALLOWED_DOMAINS`

### Frontend

Framework and shape:

- React 19 + Vite 7
- state is centralized in `frontend/src/App.jsx` using `useReducer`
- status polling lives in `frontend/src/hooks/useWorkflow.js`
- agent config loading lives in `frontend/src/hooks/useAgents.js`
- styling is centralized in `frontend/src/styles/app.css`

Important frontend conventions:

- the canonical typo is `extracter`, not `extractor`
- `useAgents.js` normalizes aliases like `extractor -> extracter`
- the frontend currently knows about `evaluation`, but not the backend-only `output` node

## Workflow Stages

Configured agent stages in `backend/data/agents.json`:

1. `normalizer`
2. `extracter`
3. `verifier`
4. `generator`
5. `latex`

Runtime-managed stages in `backend/src/server.py`:

6. `evaluation`
7. `output`

What each stage does:

- `normalizer`: parses the user prompt into structured config
- `extracter`: reads uploaded files and extracts source questions
- `verifier`: validates answers and fills in explanations/missing correctness details
- `generator`: creates isomorphic questions
- `latex`: produces LaTeX and triggers PDF compilation through the shared container runtime
- `evaluation`: runs OpenAI Evals and writes evaluation artifacts
- `output`: writes `final_result.json`, `agent_outputs.json`, and file metadata

## API Surface

Routes currently defined in `backend/src/server.py`:

- `GET /api/health`
- `GET /api/openai/status`
- `GET /version.json`
- `POST /api/upload`
- `GET /api/sliders`
- `GET /api/agents`
- `GET /api/prompts/{prompt_name}`
- `GET /api/skills`
- `GET /api/skills/{skill_name}`
- `GET /api/evaluation/files`
- `GET /api/workflow/{workflow_id}/status`
- `GET /api/workflow/{workflow_id}/result`
- `GET /api/download/{workflow_id}/{filename}`
- `POST /api/workflow/start`
- `POST /api/create-data/start`
- `GET /api/create-data/{batch_id}/status`
- `GET /api/create-data/{batch_id}/result`
- `GET /api/workflow/{workflow_id}/stream`

Important behavior:

- uploads are limited to 5 files
- max file size defaults to 20 MB each
- allowed extensions are `.pdf`, `.docx`, `.doc`, `.txt`
- `/api/workflow/{id}/stream` provides SSE updates, but the current frontend primarily polls

## Evaluation System

Evaluation lives in `backend/src/evaluation.py`.

Current behavior:

- rubric loads from `backend/data/rubric.json`
- question-level criteria are scored on a `0-100` scale
- passing threshold is `70.0`
- LaTeX validity is checked separately by verifying both `.tex` and compiled `.pdf` artifacts exist
- report output may include `report_url` when available from the OpenAI eval run

Artifacts written per run:

- `evaluation_report.json`
- `evaluation_dataset.jsonl`

## Batch / Create Data Flow

The UI’s "Create Data" flow is a batch runner over files in `backend/data/evaluation/`.

Routes:

- `POST /api/create-data/start`
- `GET /api/create-data/{batch_id}/status`
- `GET /api/create-data/{batch_id}/result`

Batch outputs:

- nested workflow output folders under a timestamped batch directory
- `batch_summary.json` at the batch root

Concurrency:

- controlled by `BATCH_WORKFLOW_CONCURRENCY`
- default is `2`

## Important Files To Edit For Common Tasks

Change models/reasoning/tool usage:

- `backend/data/agents.json`

Change agent behavior/instructions:

- `backend/data/prompts/*.txt`
- `backend/data/skills/**`

Change workflow orchestration or API behavior:

- `backend/src/server.py`
- `backend/src/main.py`
- `backend/src/helpers.py`
- `backend/src/schemas.py`

Change eval scoring/reporting:

- `backend/src/evaluation.py`
- `backend/data/rubric.json`

Change frontend UX/state:

- `frontend/src/App.jsx`
- `frontend/src/hooks/useWorkflow.js`
- `frontend/src/hooks/useAgents.js`
- `frontend/src/components/**`
- `frontend/src/styles/app.css`

## Deployment Notes

Deployment-related docs/scripts exist, but they are oriented around Raspberry Pi + nginx + systemd:

- `deploy/raspi-deploy.sh`
- `docs/DEPLOYMENT_RPI_NGINX.md`
- `docs/LIVE.md`

Current deployment assumptions in repo docs:

- nginx serves built frontend files
- uvicorn runs backend API on localhost
- `.env` is copied during Pi deploy

Treat deployment docs as operational guides, not as the source of truth for local development commands.

## Agent Warnings / Gotchas

- Use `backend/src/...` paths, not the historical `src/...` paths in older docs.
- Do not assume root npm scripts exist.
- Use `extracter` exactly as spelled in config and most backend/frontend code.
- The frontend pipeline currently ends at `evaluation`, while the backend workflow record also tracks an `output` stage.
- `frontend/README.md` is not meaningful project documentation.
- `dist/` is generated output and is rebuilt automatically by `backend/src/run.py` when stale.
- Workflow state is in memory only; restarting the server drops active workflow records.
- The app has no ORM and no database layer.

## Local Visual Debugging

Once the server is running, browser-based inspection is straightforward. One working pattern is:

```bash
npx playwright install chromium
npx playwright screenshot --browser=chromium --full-page http://127.0.0.1:8000 /tmp/app.png
```

This is useful for agent-driven UI inspection and screenshot capture.
