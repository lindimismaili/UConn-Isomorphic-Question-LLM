# UConn Isomorphic Question Generator

Generate original, isomorphic physics multiple-choice questions from source PDFs/DOCX/TXT using a 5-agent workflow:

`Normalizer -> Extracter -> Verifier -> Generator -> LaTeX Formatter`

## Features

- 5-agent pipeline with structured outputs
- Web UI + FastAPI backend
- PDF/DOCX/TXT upload support
- LaTeX output (`isomorphic_questions.tex`)
- OpenAI Evals report per run

## Repository Layout

- `src/`
  - `src/server.py` FastAPI API server
  - `src/main.py` CLI workflow runner
  - `src/run.py` server startup wrapper
  - `src/evaluation.py` OpenAI Evals integration
- `data/`
  - `data/prompts/` agent prompts
  - `data/agents.json` agent model/tool config
  - `data/sliders.json` UI slider prompt config
  - `data/rubric.json` eval rubric
- Frontend (repo root)
  - `index.html`, `styles.css`, `script.js`
- Runtime directories
  - `uploads/` uploaded files by session
  - `outputs/` workflow outputs by timestamp
- Historical artifacts
  - `archive/`

## Prerequisites

- Python 3.10+
- `OPENAI_API_KEY` set in environment (or `.env`)

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run Server

```bash
python3 src/run.py
```

Open [http://localhost:8000](http://localhost:8000).

## Run CLI

```bash
python3 src/main.py "archive/Question Data Files/PDF/Quiz_Compilation.pdf" -n 2 -i "Make questions harder"
```

## Output Files

Each run writes to `outputs/<timestamp>/`:

- `final_result.json`
- `isomorphic_questions.tex`
- `1_normalizer.json` ... `5_latex.json`
- `evaluation_report.json` (if eval ran)
- `evaluation_dataset.jsonl` (if eval ran)

## Deployment Docs

- Local/dev runbook: `docs/RUN.md`
- Live/cloud notes: `docs/LIVE.md`
- Raspberry Pi + Nginx deployment: `docs/DEPLOYMENT_RPI_NGINX.md`
