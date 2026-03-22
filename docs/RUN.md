# Run Guide

## Prerequisites

- Python 3.10+
- `OPENAI_API_KEY` configured in env or `.env`

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

## Start Web App

From repository root:

```bash
npm install
npm start
```

Open `http://localhost:8000`.

Optional flags:

- `--port` (default `8000`)
- `--host` (default `0.0.0.0`)
- `--reload` (dev autoreload)

## Dev UI (hot reload)

In two terminals:

```bash
npm install
npm run dev
```

```bash
# (backend is started automatically)
```

## Run CLI Workflow

```bash
python3 -m backend.src.main "archive/Question Data Files/PDF/Quiz_Compilation.pdf" -n 2 -i "Make questions harder"
```

## Outputs

Each run writes to `backend/outputs/<timestamp>/` with:

- `final_result.json`
- `isomorphic_questions.tex`
- `1_normalizer.json` ... `5_latex.json`
- `evaluation_report.json` and `evaluation_dataset.jsonl` when evals run
