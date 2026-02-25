# UConn Isomorphic Question Generator — CLAUDE.md

## Project Overview

This tool generates isomorphic physics multiple-choice questions from uploaded source documents (PDF/DOCX/TXT) using a 5-agent AI pipeline. "Isomorphic" means structurally equivalent questions — same physics principle, different scenario/numbers. The output is a LaTeX-formatted question set.

**Pipeline:** Normalizer → Extracter → Verifier → Generator → LaTeX Formatter

---

## Architecture

- **Backend:** FastAPI + Uvicorn (`src/server.py`) — all API routes and static file serving
- **Frontend:** React 19 + Vite 7 (`frontend/src/`) — SPA with polling-based workflow status
- **AI Agents:** OpenAI Agents SDK (`openai-agents`) — 5 sequential agents with structured outputs
- **Serving:** `dist/` (built frontend) is served by FastAPI. If `dist/` doesn't exist, falls back to repo root.

---

## Key Files

| Path | Purpose |
|------|---------|
| `src/server.py` | FastAPI server: all API routes, workflow management, file handling |
| `src/main.py` | CLI workflow runner (no web server) |
| `src/evaluation.py` | OpenAI Evals integration for quality scoring |
| `data/agents.json` | Agent model/reasoning/tool config (edit to change models) |
| `data/prompts/*.txt` | System prompts for each agent (`normalizer`, `extracter`, `verifier`, `generator`, `latex`) |
| `data/sliders.json` | UI slider config (difficulty, theme, numeric variation) |
| `data/rubric.json` | 9-criteria evaluation rubric |
| `data/skills/` | Agent skill definitions (SKILL.md files per skill) |
| `data/evaluation/` | Test PDF/DOCX files for demo runs |
| `frontend/src/App.jsx` | Root React component, all state via `useReducer` |
| `frontend/src/hooks/useWorkflow.js` | Workflow lifecycle: upload → start → poll → complete |
| `frontend/src/hooks/useAgents.js` | Loads agents config from `/api/agents` on mount |
| `frontend/src/styles/app.css` | Entire design system — single CSS file |

---

## Dev Commands

```bash
# Install (one-time)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
npm install

# Development (hot reload — backend on :8000, frontend dev server on :5173)
npm run dev

# Production build + serve (frontend built to dist/, served by FastAPI)
npm start

# Frontend build only
npm run build          # or: npm run frontend:build

# Lint
npm run lint

# CLI (no web server)
python3 src/main.py "path/to/file.pdf" -n 2 -i "Make questions harder"
```

**Environment:** Set `OPENAI_API_KEY` in `.env` before running.

---

## API Routes (FastAPI)

| Method | Route | Purpose |
|--------|-------|---------|
| GET | `/api/health` | Server health + environment status |
| GET | `/api/agents` | Agent config from `data/agents.json` |
| GET | `/api/sliders` | UI slider config |
| GET | `/api/prompts/{name}` | Agent prompt text |
| GET | `/api/skills` | List available skills |
| GET | `/api/evaluation/files` | Test files in `data/evaluation/` |
| POST | `/api/upload` | Upload files, returns `sessionId` |
| POST | `/api/workflow/start` | Start workflow, returns `workflowId` |
| GET | `/api/workflow/{id}/status` | Poll workflow status (agents, progress) |
| GET | `/api/workflow/{id}/result` | Get final result when complete |
| GET | `/api/workflow/{id}/stream` | SSE streaming of status updates |
| GET | `/api/download/{id}/{filename}` | Download output file |

---

## Frontend State (App.jsx)

State is managed by a single `useReducer`. Key fields: `files`, `agents`, `isRunning`, `workflowId`, `logs`, `result`, `error`, `activeModal`, `pipelineVisible`.

**Agent statuses:** `inactive` → `queued` → `thinking` → `completed` / `errored`

**Modal keys** (set in `activeModal`): `files`, `testFiles`, `agentDetail`, `setup`, `help`, `skills`, `promptViewer`

---

## AI Agent Pipeline

Each agent uses a gpt model (configured in `data/agents.json`) with optional Code Interpreter and reasoning effort.

1. **Normalizer** — Parses user prompt into structured config (num questions, difficulty, theme)
2. **Extracter** — Reads uploaded files via Code Interpreter (PyPDF2, pdfplumber, python-docx); extracts all MC questions
3. **Verifier** — Validates answers with Python math; adds explanations; fills in missing answer keys
4. **Generator** — Creates N isomorphic questions per source question; verifies solvability via Code Interpreter
5. **LaTeX Formatter** — Produces a complete, compilable `.tex` document

All agents share a single OpenAI Code Interpreter container per workflow run.

---

## Output Files

Each run saves to `outputs/<timestamp>/`:

- `final_result.json` — Complete `WorkflowResult` (all stages)
- `isomorphic_questions.tex` — Compilable LaTeX document
- `1_normalizer.json` … `5_latex.json` — Per-agent logs and outputs
- `evaluation_report.json` — Evals results (if run)
- `evaluation_dataset.jsonl` — Evals dataset (if run)

---

## Conventions

- **No ORM** — backend uses plain dicts/Pydantic models; no database
- **In-memory workflow state** — `workflows` dict in `server.py`; TTL 24h, max 200 entries
- **CSS** — All styles in `frontend/src/styles/app.css`; no CSS modules or Tailwind
- **Agent config** — Edit `data/agents.json` to change models/reasoning; edit `data/prompts/*.txt` to change behavior
- **Agent key aliases** — Frontend normalizes `extractor` → `extracter` (use `extracter` in backend/config)
- **File limits** — Max 5 files, 20 MB each; allowed: `.pdf`, `.docx`, `.doc`, `.txt`
- **Path security** — All file access goes through `_is_under_directory()` in `server.py`

---

## Deployment

- **Local dev:** `npm run dev`
- **Production:** `npm start` (builds frontend, starts server on port 8000)
- **Raspberry Pi:** `bash raspi-deploy.sh` — sets up systemd service; auto-deploys on `git pull` via `.githooks/`
- **Nginx:** Optional reverse proxy — see `docs/DEPLOYMENT_RPI_NGINX.md`
- **Logs:** `deploy-hook.log` (Pi auto-deploy), stdout/stderr from uvicorn
