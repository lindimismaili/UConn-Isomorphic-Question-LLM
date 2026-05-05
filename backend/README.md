# Backend

FastAPI server and workflow engine for the UConn Isomorphic Question Generator.

This package owns:

- API endpoints for uploads, workflow execution, status, downloads, and evaluation
- the CLI workflow runner
- shared OpenAI container setup and LaTeX compilation
- artifact writing and run metadata
- evaluation outputs and batch create-data jobs

## Architecture

The backend is centered around the workflow pipeline in `backend/src/main.py` and the API layer in `backend/src/server.py`.

Key modules:

- `backend/src/server.py` FastAPI routes and browser-facing workflow orchestration
- `backend/src/main.py` CLI workflow runner
- `backend/src/run.py` server startup wrapper that builds the frontend when needed
- `backend/src/container.py` shared OpenAI container and LaTeX helper runtime
- `backend/src/artifacts.py` agent artifact readers and writers
- `backend/src/evaluation.py` rubric loading and OpenAI eval execution
- `backend/src/helpers.py` prompt loading, config reconciliation, and file upload helpers
- `backend/src/schemas.py` Pydantic models for workflow input and output

The workflow uses one shared OpenAI container per run so the agents and the LaTeX compiler work against the same runtime context.

## Data Folders

- `backend/data/agents.json` agent configuration loaded by the frontend and backend
- `backend/data/prompts/` prompt text for each agent stage
- `backend/data/skills/` agent skill definitions and helper scripts
- `backend/data/rubric.json` evaluation rubric configuration
- `backend/data/template.tex` LaTeX template material
- `backend/data/dropdown.json` UI dropdown data
- `backend/data/evaluation/` fixture PDFs used by the app and testing flows
- `backend/uploads/` uploaded source files by session
- `backend/outputs/` timestamped workflow results and evaluation artifacts

## API and CLI Entry Points

API server:

```bash
python -m backend.src.run --reload
```

Direct Uvicorn entry point:

```bash
python -m backend.src.server
```

CLI workflow runner:

```bash
python -m backend.src.main <files...> -n 2 -i "extra instructions"
```

CLI flags:

- positional files: PDFs, DOCX, DOC, or TXT files
- `-n`, `--num-questions`: generated questions per source question
- `-i`, `--instructions`: extra workflow instructions

If no file is passed, the CLI looks for the default evaluation PDF under the repo's question data folder.

## Run Outputs

Workflow runs write timestamped folders under `backend/outputs/`.

Typical contents:

- `final_result.json`
- `isomorphic_questions.tex`
- `isomorphic_questions.pdf`
- `agent_outputs/agent_metadata_output.json`
- `agent_outputs/<agent>/agent_input.jsonl`
- `agent_outputs/<agent>/agent_output.jsonl`
- `evaluation_outputs/evaluation_report.json`
- `evaluation_outputs/evaluation_dataset.jsonl`
- `evaluation_outputs/evaluation_items.jsonl`

The API also supports download and status endpoints for these artifacts.

## Agent Testing Fixtures

Saved source files and stage-specific agent inputs for regression testing live in `backend/data/testing/`.
Use them with the UI's `Input To Agent` workflow.
Additional local developer checks live in `backend/src/style_testing.py`, `backend/src/check_generator_style.py`, and `backend/tests/test_style_testing.py`. They are for manual regression testing only and are not used by runtime workflow execution.

## Maintenance Notes

- Keep `backend/data/agents.json`, `backend/data/prompts/`, and `backend/data/rubric.json` aligned with the workflow stages.
- Do not edit generated files in `backend/outputs/` or `backend/uploads/`.
- The backend loads `.env` from the repo root when present.
- The server serves the built frontend from `dist/` when available; if `dist/index.html` is missing, the root route falls back to a simple diagnostic page.
- Health checks are available at `/api/health` and `/api/openai/status`.

## Environment Variables

Relevant backend settings include:

- `OPENAI_API_KEY`
- `HOST`
- `PORT`
- `MAX_UPLOAD_BYTES`
- `WORKFLOW_TTL_SECONDS`
- `WORKFLOW_MAX_ENTRIES`
- `BATCH_WORKFLOW_CONCURRENCY`
- `OPENAI_STATUS_TIMEOUT_SECONDS`
- `OPENAI_CONTAINER_EXPIRES_MINUTES`
- `OPENAI_CONTAINER_BOOTSTRAP_MODEL`
- `OPENAI_CONTAINER_ALLOWED_DOMAINS`
