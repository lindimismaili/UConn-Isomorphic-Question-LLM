# UConn Isomorphic Question LLM

Generates fully original, isomorphic multiple-choice questions from a set of source questions using an agent workflow (normalize → extract → verify → generate).

## Repo layout

- `Project/main.py` — agent workflow (normalizer, extractor, verifier, generator).
- `Question Data Files/` — source PDFs/DOCX used as input material.
- `GPT Builder Versions/` — prompt/schema iterations and reference artifacts.
- `Assets/` — project assets (e.g., icon).

## Prerequisites

- Python 3.10+ recommended
- An OpenAI API key in `OPENAI_API_KEY`

## Install

Create and activate a virtualenv, then install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install openai pydantic openai-agents
```

## Run (developer usage)

`Project/main.py` exposes an async entrypoint `run_workflow(workflow_input: WorkflowInput)` that expects `input_as_text`.

Example:

```bash
OPENAI_API_KEY=... python -c "import asyncio, sys; sys.path.append('Project'); import main; asyncio.run(main.run_workflow(main.WorkflowInput(input_as_text='Paste questions / instructions here.')))"
```

Notes:
- The workflow is currently designed around agent interactions and may need small adjustments (e.g., returning `generation_result`) depending on how you want to consume outputs.
- If you want to feed PDF/DOCX files directly, add a preprocessing step to extract text and pass it into `input_as_text`.

