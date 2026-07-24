# UConn Isomorphic Question Generator

Generate original, isomorphic physics questions from source PDFs, DOCX files, or text files.
The project uses a multi-agent workflow that normalizes the request, extracts source questions, verifies them, generates new questions, formats LaTeX, and runs evaluation.

## Purpose

The app takes question sources and produces a fresh set of physics questions that preserve the original reasoning pattern while changing the surface details.

The core workflow is:

`Setup -> Input Guardrail -> Normalizer -> Extracter -> Content Guardrail -> Verifier -> Generator -> Latexifier -> Evaluation -> Output`

Two safety guardrails run inline: an input guardrail (content moderation plus a
jailbreak / prompt-injection check on the request, before any container is created)
and a content guardrail (moderation plus an academic/physics on-topic check on the
extracted questions). If a guardrail trips, the run ends in a `blocked` state with a
reason rather than failing as an error.

For coding-agent onboarding, repo conventions, and a full tour of the
architecture, see [AGENTS.md](AGENTS.md).

## Architecture

The repository is split into two runtime surfaces:

- `site/backend/`: FastAPI API, workflow orchestration, evaluation, data files, and run outputs
- `site/frontend/`: React + Vite UI that talks to the backend API
- `site/dist/`: built frontend assets served by the backend when present

The backend creates a shared OpenAI container for each workflow run, uploads source files, executes the agent chain, compiles LaTeX to PDF, and writes the final artifacts into a timestamped output folder. Both the server and the CLI drive the same engine in `site/backend/src/workflow/engine.py`.

## Documentation

Every Markdown doc in the repo and what it covers:

- [README.md](README.md) — this file: setup, environment, run/CLI/output reference, and troubleshooting.
- [AGENTS.md](AGENTS.md) — the single source of truth for coding-agent onboarding: repo conventions, full architecture tour, source-of-truth paths, the commands that work, and the data contracts. ([CLAUDE.md](CLAUDE.md) is a pointer to it.)
- [site/backend/BACKEND.md](site/backend/BACKEND.md) — backend reference: what every file does, the important functions, the 10-node workflow, the HTTP API routes, the `data/` files, and the tests.
- [site/frontend/DESIGN.md](site/frontend/DESIGN.md) — frontend design system: tokens, the reusable component primitives, modals/stacking, motion, and the single-page design-system showcase. **Read before any UI change.**
- [deploy/DEPLOY.md](deploy/DEPLOY.md) — deployment and local-preview scripts (operational guide, not a source of truth).

## Repository Layout

- `site/backend/src/main.py` command-line workflow runner
- `site/backend/src/server.py` FastAPI app factory (CORS, lifespan, static mount)
- `site/backend/src/api/` HTTP routers by domain (health, config, files, workflows, batch) + `state.py`
- `site/backend/src/workflow/` shared engine, agent factory, payload builders, resume, SQLite store
- `site/backend/src/evaluation.py` OpenAI evals integration
- `site/backend/src/artifacts.py` workflow and agent artifact writers/readers
- `site/backend/src/container.py` shared OpenAI container runtime helpers
- `site/backend/data/` prompts, rubric, agents config, and evaluation fixtures
  (PDFs in `site/backend/data/evaluation/` are listed dynamically by the UI's
  Browse Test Files and Create Data flows — drop a file in to add an option)
- `site/backend/uploads/` uploaded source files grouped by session
- `site/backend/outputs/` timestamped workflow outputs
- `site/backend/runtime/` SQLite mirror of workflow/batch state
- `site/frontend/src/` React UI (components, hooks, utils, styles)
- `site/frontend/DESIGN.md` UI element mapping (design tokens, button/modal system)

## Prerequisites

- Python 3.10+
- Node.js 20.19+ within Node 20.x, or Node.js 22.12+ and npm. Node 22 LTS is recommended for Raspberry Pi deploys.
- `OPENAI_API_KEY` in your environment or a `.env` file in `site/`

Notes:

- The backend uses OpenAI containers and agents, so the key must be valid for those APIs.
- Normal runs do not require a local LaTeX install. LaTeX compilation happens inside the shared OpenAI container.

## Setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r site/backend/requirements.txt
npm -C site/frontend install
```

If you prefer to keep the backend and frontend dependencies separate, activate the virtual environment before running backend commands and keep the frontend install in `site/frontend/node_modules/`.

## Environment Variables

The backend loads `.env` from `site/` (its resolved repo root) if present. `.env`
is tracked in the repo; [site/.env.example](site/.env.example) documents every supported variable.
Local-only overrides belong in `.env.local` / `.env.*.local` (gitignored).

Required:

- `OPENAI_API_KEY` - required for workflow execution and uploads

Backend runtime settings:

- `HOST` - bind host for `python -m backend.src.server` defaults
- `PORT` - bind port for the FastAPI app defaults
- `MAX_UPLOAD_BYTES` - per-file upload size limit in bytes, default `20971520`
- `MAX_UPLOAD_TOTAL_BYTES` - per-request total upload cap across all files in bytes, default `62914560` (60 MB)
- `WORKFLOW_TTL_SECONDS` - how long workflow records stay in memory, default `86400`
- `WORKFLOW_MAX_ENTRIES` - max workflow/batch records kept in memory, default `200`
- `PRUNE_INTERVAL_SECONDS` - background prune cadence, default `60`
- `BATCH_WORKFLOW_CONCURRENCY` - concurrent create-data batch limit, default `2`
- `OPENAI_STATUS_TIMEOUT_SECONDS` - timeout for the OpenAI status probe, default `5`
- `OPENAI_CONTAINER_EXPIRES_MINUTES` - shared container lifetime, clamped to `1..20`, default `20`
- `OPENAI_CONTAINER_BOOTSTRAP_MODEL` - model used to bootstrap the shared container, default `gpt-5.6-luna`
- `OPENAI_CONTAINER_ALLOWED_DOMAINS` - comma-separated allowlist for container bootstrap and tools
- `GUARDRAILS_ENABLED` - toggle the input/content guardrails (overrides the `agents.json` default)
- `GRADER_MODEL_OVERRIDE` - grade with a different model than the one in `agents.json`

Optional per-agent turn caps live in `agents.json` as `maxTurns` (default `6`, latexifier `12`).

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

To run only the backend API (`--app-dir site` puts `backend` on the import path):

```bash
python -m uvicorn backend.src.server:app --app-dir site --host 0.0.0.0 --port 8000 --reload
```

To serve the built frontend from the backend, build `site/dist/` first:

```bash
bash deploy/preview.sh --web
```

## Dev UI

Run the Vite dev server in a second terminal:

```bash
npm -C site/frontend run dev:ui
```

The Vite dev server proxies API requests to the local backend started by `deploy/preview.sh`.
If your backend is elsewhere, set `VITE_BACKEND_URL` before starting Vite.

Useful frontend commands:

```bash
npm -C site/frontend run build
npm -C site/frontend run lint
npm -C site/frontend run preview
```

## CLI Usage

The CLI has full parity with the web app over the shared engine, exposed as
argparse subcommands: `run`, `rerun-from-agent`, `from-agent-input`, `batch`,
`eval`, `improve`, `show`, `zip`, `list` (`agents|themes|prompts|rubric|eval-files|runs`),
and `prompt`. Run `python -m backend.src.main --help` (works without `OPENAI_API_KEY`)
for the full list.

The classic flat invocation still works (leading files/flags dispatch to `run`):

```bash
PYTHONPATH=site python -m backend.src.main "archive/Question Data Files/PDF/Quiz_Compilation.pdf" -n 2 -i "Make questions harder"
```

`run` flags:

- positional files: one or more PDF, DOCX, DOC, or TXT inputs
- `-n`, `--num-questions`: number of generated questions per source question
- `-i`, `--instructions`: extra instructions for the workflow

If no file is supplied, the CLI falls back to the default evaluation PDF when available.

## Prompt Versioning

Prompt templates live in `site/backend/data/prompts/*.txt`. Use Git commits as the
version history for prompt experiments, and keep prompt changes in dedicated
prompt-only commits:

```bash
python scripts/prompt_versions.py status
python scripts/prompt_versions.py snapshot -m "Tune generator distractor instructions"
```

Useful rollback and inspection commands:

```bash
python scripts/prompt_versions.py list
python scripts/prompt_versions.py show <commit> generator
python scripts/prompt_versions.py restore <commit> generator
```

`restore` creates a new prompt-only commit by default so the rollback itself is
also recorded. Add `--no-commit` only when you need to inspect the restored files
before committing.

## Workflow Stages

The pipeline has 10 registry nodes (6 LLM agents + 2 runtime + 2 guardrail):

1. Set up the shared OpenAI container and upload source files
2. Input guardrail: moderation + jailbreak/prompt-injection check on the request
3. Normalize the user request into workflow configuration
4. Extract source questions from the uploaded material
5. Content guardrail: moderation + academic/physics on-topic check on the extracted questions
6. Verify the extracted questions and answers
7. Generate isomorphic questions
8. Format the result as LaTeX and compile a PDF
9. Evaluate the generated output and write run metadata
10. Finalize local output files

If a guardrail trips, the run ends in a `blocked` state (distinct from an error).

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

Every workflow writes to `site/backend/outputs/<timestamp>/`.

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

Batch create-data runs also write batch summaries and per-file workflow folders under `site/backend/outputs/`.

## Evaluation Artifacts

The evaluation step uses the rubric in `site/backend/data/rubric.json` and writes scored results into the output folder for the run.
Those files are the primary record of how the generated questions were judged.

## Project Docs

Every Markdown doc tracked in the repo:

- [AGENTS.md](AGENTS.md) the single source of truth: architecture, source-of-truth paths, conventions, API surface, gotchas
- [CLAUDE.md](CLAUDE.md) coding-agent entry point; points straight to AGENTS.md
- [site/frontend/DESIGN.md](site/frontend/DESIGN.md) frontend design system: tokens, layout, buttons, modals, themes, transitions, consistency rules
- [deploy/DEPLOY.md](deploy/DEPLOY.md) deployment and local preview scripts
- [README.md](README.md) this file: setup, environment, run/CLI/output reference

## Troubleshooting

- `OPENAI_API_KEY` missing: the backend API can start, but workflow execution will fail until the key is set.
- `site/dist/index.html` missing: run `npm -C site/frontend run build`, then restart the backend.
- `crypto.hash is not a function` or Vite says Node 20.19+/22.12+ is required: upgrade Node. Node 21 is not in Vite's allowed range. On Raspberry Pi, run `bash deploy/deploy.sh`; the deploy script installs Node 22 from NodeSource when the current Node is missing or too old.
- Upload rejected: check the file extension and `MAX_UPLOAD_BYTES`.
- Workflow appears stuck: check `/api/health`, `/api/openai/status`, and the latest folder in `site/backend/outputs/`.
- Frontend cannot reach the backend in dev mode: run `bash deploy/preview.sh --dev` and open `http://isomorphic.localhost:5173`.

## Raspberry Pi Automatic Deployment

The Pi deployer treats `origin/main` as the only automatic deployment source.
It fetches a commit without merging, builds that commit in an isolated release
directory, then atomically changes the `current` release symlink only after the
candidate is ready. The prior three known-good releases remain on disk for
rollback.

- User data survives every deployment under the persistent `uploads/`,
  `outputs/`, and `runtime/` directories. This includes generated questions,
  evaluations, uploaded files, and workflow-history SQLite state.
- A failed fetch, build, restart, or health check leaves the existing release
  running or restores it automatically. The failed commit is quarantined until
  `origin/main` changes, so the 60-second timer does not rebuild a known-bad
  commit forever.
- `GET /api/deployment/status` exposes a sanitized summary and recent bounded
  deployment events for the future local dashboard. Detailed diagnostics remain
  in `journalctl -u uconn-isomorphic-update.service`.

## Secure Operations Dashboard

`/admin` is a protected operations dashboard for output/archive browsing,
workflow history, evaluations, maintenance mode, stage-cache controls, health
reports, and incident/audit history. Configure Cloudflare Access to protect
`/admin*` and `/api/admin/*` with Google sign-in, then set
`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, and a comma-separated `ADMIN_EMAILS`
allowlist only on the Pi. The server validates the signed Access assertion
itself; a Cloudflare page rule alone is not sufficient.

### Local dashboard testing

This checkout includes the ignored `site/.env.local` file so a local run can
exercise the full dashboard without Cloudflare. It contains a non-production
test identity:

```env
ADMIN_LOCAL_DEV_MODE=true
ADMIN_LOCAL_DEV_EMAIL=local-admin@example.test
```

Start the site bound to `127.0.0.1`/`localhost`, then visit
`http://localhost:8000/admin`. This bypass is accepted only from a direct
loopback connection and only while both variables are set; it must never be
placed in the Pi runtime configuration. It exercises the real local file,
workflow, maintenance, cache, and download controls.

Keep `site/.env.local` in your local checkout. It is intentionally gitignored
so it cannot be committed or deployed by accident.

The dashboard stores non-secret state in `runtime/operations.sqlite3`. The Pi
installer creates a release-independent `admin-runtime.env` override for an
admin-replaced `OPENAI_API_KEY` and optional `OPENAI_ADMIN_KEY`; neither is
returned through the API. Optional SMTP alert settings are `SMTP_HOST`,
`SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, and
`ALERT_RECIPIENTS`. The operations timer writes a sanitized Pi/cloudflared
report every minute.

Before pushing to `main`, test changes that affect startup, dependencies,
database/runtime compatibility, uploads, or output artifacts. Runtime-data
changes must be backward compatible with the prior release or include an
explicit migration and rollback path; automatic deployment can roll back code,
but it cannot safely undo destructive data migrations.

## Development Notes

- Do not edit files under `site/backend/outputs/` or `site/backend/uploads/`; they are generated runtime data.
- The backend serves the built frontend from `site/dist/` when available, otherwise it returns a fallback page that points to the dev commands.
- The Vite build emits `agents.json` and `data/agents.json` so the UI can load the shared agent config without hard-coding it in the app bundle.
- The UI talks to `/api/upload`, `/api/workflow/start`, `/api/workflow/{id}/status`, `/api/workflow/{id}/result`, and the related workflow endpoints exposed by FastAPI.
