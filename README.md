# UConn Isomorphic Question Generator

Generate original, isomorphic physics questions from source PDFs, DOCX files, or text files.
The project uses a multi-agent workflow that normalizes the request, extracts source questions, verifies them, generates new questions, formats LaTeX, and runs evaluation.

## Purpose

The app takes question sources and produces a fresh set of physics questions that preserve the original reasoning pattern while changing the surface details.

The core workflow is:

`Normalizer -> Extracter -> Verifier -> Generator -> Latexifier -> Evaluation -> Output`

For coding-agent onboarding, repo conventions, and a full tour of the
architecture, see [AGENTS.md](AGENTS.md).

## Architecture

The repository is split into two runtime surfaces:

- `backend/`: FastAPI API, workflow orchestration, evaluation, data files, and run outputs
- `frontend/`: React + Vite UI that talks to the backend API
- `dist/`: built frontend assets served by the backend when present

The backend creates a shared OpenAI container for each workflow run, uploads source files, executes the agent chain, compiles LaTeX to PDF, and writes the final artifacts into a timestamped output folder. Both the server and the CLI drive the same engine in `backend/src/workflow/engine.py`.

## Repository Layout

- `backend/src/main.py` command-line workflow runner
- `backend/src/server.py` FastAPI app factory (CORS, lifespan, static mount)
- `backend/src/api/` HTTP routers by domain (health, config, files, workflows, batch) + `state.py`
- `backend/src/workflow/` shared engine, agent factory, payload builders, resume, SQLite store
- `backend/src/evaluation.py` OpenAI evals integration
- `backend/src/artifacts.py` workflow and agent artifact writers/readers
- `backend/src/container.py` shared OpenAI container runtime helpers
- `backend/data/` prompts, rubric, agents config, and evaluation fixtures
  (PDFs in `backend/data/evaluation/` are listed dynamically by the UI's
  Browse Test Files and Create Data flows — drop a file in to add an option)
- `backend/uploads/` uploaded source files grouped by session
- `backend/outputs/` timestamped workflow outputs
- `backend/runtime/` SQLite mirror of workflow/batch state
- `frontend/src/` React UI (components, hooks, utils, styles)
- `frontend/DESIGN.md` UI element mapping (design tokens, button/modal system)

## Prerequisites

- Python 3.10+
- Node.js 20.19+ within Node 20.x, or Node.js 22.12+ and npm. Node 22 LTS is recommended for Raspberry Pi deploys.
- `OPENAI_API_KEY` in your environment or a `.env` file at the repo root

Notes:

- The backend uses OpenAI containers and agents, so the key must be valid for those APIs.
- Normal runs do not require a local LaTeX install. LaTeX compilation happens inside the shared OpenAI container.

## Setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
npm -C frontend install
```

If you prefer to keep the backend and frontend dependencies separate, activate the virtual environment before running backend commands and keep the frontend install in `frontend/node_modules/`.

## Environment Variables

The backend loads `.env` from the repo root if present. `.env` is gitignored —
copy [.env.example](.env.example) to `.env` and fill it in.

Required:

- `OPENAI_API_KEY` - required for workflow execution and uploads

Backend runtime settings:

- `HOST` - bind host for `python -m backend.src.server` defaults
- `PORT` - bind port for the FastAPI app defaults
- `MAX_UPLOAD_BYTES` - upload size limit in bytes, default `20971520`
- `WORKFLOW_TTL_SECONDS` - how long workflow records stay in memory, default `86400`
- `WORKFLOW_MAX_ENTRIES` - max workflow/batch records kept in memory, default `200`
- `PRUNE_INTERVAL_SECONDS` - background prune cadence, default `60`
- `BATCH_WORKFLOW_CONCURRENCY` - concurrent create-data batch limit, default `2`
- `OPENAI_STATUS_TIMEOUT_SECONDS` - timeout for the OpenAI status probe, default `5`
- `OPENAI_CONTAINER_EXPIRES_MINUTES` - shared container lifetime, clamped to `1..20`, default `20`
- `OPENAI_CONTAINER_BOOTSTRAP_MODEL` - model used to bootstrap the shared container, default `gpt-5.5`
- `OPENAI_CONTAINER_ALLOWED_DOMAINS` - comma-separated allowlist for container bootstrap and tools

Frontend dev proxy:

- `VITE_BACKEND_URL` - backend URL used by the Vite dev server proxy, default `http://127.0.0.1:8000`

## Local Server

Run the local backend plus Vite hot-reload UI:

```bash
bash deploy/preview.sh --dev
```

Open:

- `http://isomorphic.localhost:5173` for the Vite UI
- `http://isomorphic.localhost:5173/api/health` for a quick backend check through the dev proxy

To run only the backend API:

```bash
python -m uvicorn backend.src.server:app --host 0.0.0.0 --port 8000 --reload
```

To serve the built frontend from the backend, build `dist/` first:

```bash
bash deploy/preview.sh --web
```

## Dev UI

Run the Vite dev server in a second terminal:

```bash
npm -C frontend run dev:ui
```

The Vite dev server proxies API requests to the local backend started by `deploy/preview.sh`.
If your backend is elsewhere, set `VITE_BACKEND_URL` before starting Vite.

Useful frontend commands:

```bash
npm -C frontend run build
npm -C frontend run lint
npm -C frontend run preview
```

## CLI Usage

Run the workflow directly from the command line:

```bash
python -m backend.src.main "archive/Question Data Files/PDF/Quiz_Compilation.pdf" -n 2 -i "Make questions harder"
```

CLI flags:

- positional files: one or more PDF, DOCX, DOC, or TXT inputs
- `-n`, `--num-questions`: number of generated questions per source question
- `-i`, `--instructions`: extra instructions for the workflow

If no file is supplied, the CLI falls back to the default evaluation PDF when available.

## Workflow Stages

1. Upload source files to OpenAI
2. Normalize the user request into workflow configuration
3. Extract source questions from the uploaded material
4. Verify the extracted questions and answers
5. Generate isomorphic questions
6. Format the result as LaTeX and compile a PDF
7. Evaluate the generated output and write run metadata
8. Finalize local output files

The frontend mirrors these stages in the pipeline view, logs, and result panels.
Clicking a pipeline block opens its detail modal: agents get a wide
input/output inspector, while the runtime blocks open "Starting Runtime"
(runtime parameters plus the run's input files/prompt) and "Ending Runtime"
(inputs plus every output file with view/download). After a run completes, the
results panel offers three modals — Results (config, verified source
questions with answers and distractor logic, generated questions, evaluation
summary), Evaluations (per-item judge results with criterion drill-down), and
Agents (run summary, per-agent metadata, and artifact viewers).

## Outputs

Every workflow writes to `backend/outputs/<timestamp>/`.

Common files:

- `final_result.json`
- `isomorphic_questions.tex`
- `isomorphic_questions.pdf`
- `agent_outputs/agent_metadata_output.json`

Agent artifacts:

- `agent_outputs/<agent>/agent_input.jsonl`
- `agent_outputs/<agent>/agent_output.jsonl`

Evaluation artifacts:

- `evaluation_outputs/evaluation_report.json`
- `evaluation_outputs/evaluation_dataset.jsonl`
- `evaluation_outputs/evaluation_agents.jsonl`

Batch create-data runs also write batch summaries and per-file workflow folders under `backend/outputs/`.

## Evaluation Artifacts

The evaluation step uses the rubric in `backend/data/rubric.json` and writes scored results into the output folder for the run.
Those files are the primary record of how the generated questions were judged.

## Project Docs

Every Markdown doc tracked in the repo:

- [AGENTS.md](AGENTS.md) the single source of truth: architecture, source-of-truth paths, conventions, API surface, gotchas
- [CLAUDE.md](CLAUDE.md) coding-agent entry point; points straight to AGENTS.md
- [frontend/DESIGN.md](frontend/DESIGN.md) frontend design system: tokens, layout, buttons, modals, themes, transitions, consistency rules
- [deploy/DEPLOY.md](deploy/DEPLOY.md) deployment and local preview scripts
- [README.md](README.md) this file: setup, environment, run/CLI/output reference

## Troubleshooting

- `OPENAI_API_KEY` missing: the backend API can start, but workflow execution will fail until the key is set.
- `dist/index.html` missing: run `npm -C frontend run build`, then restart `python -m backend.src.server`.
- `crypto.hash is not a function` or Vite says Node 20.19+/22.12+ is required: upgrade Node. Node 21 is not in Vite's allowed range. On Raspberry Pi, run `bash deploy/deploy.sh`; the deploy script installs Node 22 from NodeSource when the current Node is missing or too old.
- Upload rejected: check the file extension and `MAX_UPLOAD_BYTES`.
- Workflow appears stuck: check `/api/health`, `/api/openai/status`, and the latest folder in `backend/outputs/`.
- Frontend cannot reach the backend in dev mode: run `bash deploy/preview.sh --dev` and open `http://isomorphic.localhost:5173`.

## Development Notes

- Do not edit files under `backend/outputs/` or `backend/uploads/`; they are generated runtime data.
- The backend serves the built frontend from `dist/` when available, otherwise it returns a fallback page that points to the dev commands.
- The Vite build emits `agents.json` and `data/agents.json` so the UI can load the shared agent config without hard-coding it in the app bundle.
- The UI talks to `/api/upload`, `/api/workflow/start`, `/api/workflow/{id}/status`, `/api/workflow/{id}/result`, and the related workflow endpoints exposed by FastAPI.
