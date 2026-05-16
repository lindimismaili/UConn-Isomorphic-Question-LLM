# UConn Isomorphic Question Generator — CLAUDE.md

This file is the Claude-oriented companion to `.codex/AGENTS.md`.

Use `.codex/AGENTS.md` as the primary source of truth. This file is a shorter project brief with the same current-repo assumptions.

## Project Summary

This repo is a FastAPI + React app for generating isomorphic physics multiple-choice questions from uploaded PDFs/DOCX/TXT files using the OpenAI Agents SDK.

User-facing pipeline:

`Normalizer -> Extracter -> Verifier -> Generator -> LaTeX Formatter`

Actual runtime pipeline today:

`setup -> normalizer -> extracter -> verifier -> generator -> latex -> evaluation -> output`

Important distinction:

- only the 5 core agents are configured in `backend/data/agents.json`
- `evaluation` and `output` are orchestrated in backend code
- the frontend currently shows `evaluation` as the final visible stage

## Active Code Paths

Use these paths:

- `backend/src/server.py`
- `backend/src/run.py`
- `backend/src/main.py`
- `backend/src/container.py`
- `backend/src/evaluation.py`
- `backend/src/judge_agreement.py` (judge ↔ professor κ/agreement metrics)
- `backend/src/pipeline_validation.py` (includes deterministic distractor_logic preservation check)
- `Other/compare_to_professor.py` (CLI: per-criterion κ + confusion vs `Other/Evaluation.xlsx`)
- `docs/ANALYSIS.md` (full pipeline audit + overhaul notes)
- `backend/data/agents.json`
- `frontend/src/App.jsx`
- `frontend/src/hooks/useWorkflow.js`
- `frontend/src/hooks/useAgents.js`
- `frontend/src/styles/app.css`

Do not rely on the old `.claude/CLAUDE.md` path references like `src/server.py`. The live code is under `backend/src/`.

## Commands That Actually Work

Setup:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r backend/requirements.txt
npm -C frontend install
```

Run the full app:

```bash
.venv/bin/python -m backend.src.run --host 127.0.0.1 --port 8000
```

Frontend-only Vite dev server:

```bash
npm -C frontend run dev:ui
```

CLI workflow:

```bash
.venv/bin/python -m backend.src.main "archive/Question Data Files/PDF/Quiz_Compilation.pdf" -n 2 -i "Make questions harder"
```

## Repo Gotchas

- There is no root `package.json`.
- Legacy docs that say `npm start` or `npm run dev` from repo root are stale for this checkout.
- `frontend/package.json` still contains delegating scripts that target missing root scripts.
- `frontend/README.md` is just the stock Vite template.
- The canonical spelling is `extracter`, not `extractor`.
- Top-level `src/` is not the app source tree.

## Runtime / Infra Notes

- `.env` at repo root is loaded manually by backend code.
- `OPENAI_API_KEY` is required for real runs.
- Upload limit: 5 files, 20 MB each by default.
- Workflow state is stored in memory; no database.
- `backend/src/run.py` auto-builds `dist/` when frontend inputs are newer.
- Vite proxies `/api`, `/version.json`, `/data`, and `/skills` to `http://127.0.0.1:8000` by default.

## Outputs

Per workflow run:

- `backend/outputs/<timestamp>/final_result.json`
- `backend/outputs/<timestamp>/isomorphic_questions.tex`
- `backend/outputs/<timestamp>/isomorphic_questions.pdf`
- `backend/outputs/<timestamp>/evaluation_report.json`
- `backend/outputs/<timestamp>/evaluation_dataset.jsonl`
- `backend/outputs/<timestamp>/agent_outputs.json`

Batch create-data runs also produce:

- `backend/outputs/<timestamp>/batch_summary.json`

## Evaluation Notes

Evaluation is live in `backend/src/evaluation.py`.

Current behavior:

- rubric comes from `backend/data/rubric.json`
- question criteria are scored `0-100`
- passing threshold is `70.0`
- LaTeX gets a separate artifact-presence check

## Recommendation

If you need deeper repo context before editing, read `.codex/AGENTS.md` first, then inspect the specific backend or frontend file you plan to change.
