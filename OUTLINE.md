# OUTLINE

A plain-English tour of what this project is, how it's laid out, and how a run actually plays out.

## What the project does

You hand it a physics quiz (PDF or DOCX). It hands back a brand-new quiz with **isomorphic** versions of each question — same physics, same difficulty, different surface story and different numbers. Then it grades the new quiz against a rubric so you can tell whether the variants are any good.

The work is split across six small AI "agents," each with one job. They run one after another, passing their output to the next.

## The six stages

1. **Normalizer** — Reads the user's instructions ("make 3 variants per question, life-science theme") and turns them into a config the rest of the pipeline can follow.
2. **Extracter** — Opens the uploaded files, pulls every question out, and writes them down in a structured format (stem, options, answer, etc.).
3. **Verifier** — Re-checks each extracted question. Confirms the right answer, fills in option explanations, and labels the distractors with the misconception each one represents.
4. **Generator** — Writes brand-new questions that test the same physics. New story, new numbers, but the wrong-answer traps still target the same misconceptions.
5. **Latexifier** — Formats the new questions into a clean LaTeX document and compiles it to PDF.
6. **Evaluation** — Runs the rubric (`backend/data/rubric.json`) over the source/generated pairs and scores them on things like "preserve physical content," "scenario variation," "distractor logic preservation," etc. Produces a report, a dataset, and a CSV spreadsheet.

Within one run these stages are **sequential** — each one needs the previous one's output. If you upload multiple files in a single run, they get combined into one dataset, not processed in parallel.

## How a run actually plays out

1. You drop one or more files into the web UI (or pass paths to the CLI).
2. The Normalizer reads your prompt and sets up the config.
3. The Extracter uploads the files to OpenAI and pulls out the questions.
4. Verifier, Generator, Latexifier each do their part, in order.
5. The LaTeX is compiled to PDF inside an OpenAI code-interpreter container.
6. Evaluation grades the result against the rubric.
7. Everything lands in a timestamped folder under `backend/outputs/` — the source LaTeX, the compiled PDF, the structured artifacts for every agent (input + output as JSONL), and the evaluation report.

The UI polls the workflow status endpoint while a run is active, so you watch each agent flip from idle → running → done as it goes. (A Server-Sent Events `/stream` endpoint also exists, but the UI uses polling.)

## Two ways to run it

- **Web app** — `uvicorn backend.src.server:app --reload` for the API, and `cd frontend && npm run dev` for the React UI. This is the main interface: upload files, watch the pipeline, view per-agent inputs/outputs, read the eval report, download the PDF.
- **CLI** — `python backend/src/main.py path/to/quiz.pdf`. Same engine, no UI. Useful for scripting and batch jobs.

Both call the **same workflow engine** (`backend/src/workflow/engine.py`). There's no second copy of the pipeline logic anywhere — that was the point of the recent refactor.

## Features beyond the basic pipeline

- **Resume from any stage.** If the Generator output looked wrong but the Extracter was fine, you can rerun starting from the Verifier or Generator without redoing the upload and extraction. The engine reads the cached output from a previous run's artifacts.
- **Upload a saved input.** Each agent writes its input to disk. You can take one of those input files (or hand-craft one), upload it through the "Input To Agent" modal, and start a fresh run from that point. Useful for debugging a single stage.
- **Batch mode.** The `/api/create-data/start` endpoint takes one prompt and fans it out across every PDF sitting in `backend/data/evaluation/`. It runs up to 2 workflows concurrently (`BATCH_WORKFLOW_CONCURRENCY`). Each gets its own output folder; results stream back as a group.
- **Evaluation-only batch script.** `Other/runEvals.py` does the same thing but from the command line, with `--concurrency N` to control parallelism.
- **XLSX report.** `Other/createXLSX.py` rolls a batch's per-PDF JSON reports into a single spreadsheet for comparison.
- **Judge agreement / professor comparison.** `Other/compare_to_professor.py` and `backend/src/tools/judge_agreement.py` measure how well the AI grader's pass/fail calls line up with a human professor's labels (Cohen's kappa, agreement rates).
- **Workflow persistence.** Active workflows are kept in memory and mirrored to a SQLite store (`backend/runtime/workflows.sqlite3`) so the UI can show recent runs across server restarts. Old records are pruned by TTL and a max-entry cap on a background interval (not on every request).

## Project layout (high-level)

```
backend/
  src/
    server.py       # FastAPI app factory. CORS, lifespan (background prune), static mount.
    api/            # HTTP route handlers split by domain (~20 endpoints):
      health.py     #   /api/health, /api/openai/status, /version.json
      config.py     #   /api/dropdown, /api/rubric, /api/agents, /api/prompts, eval files
      files.py      #   /api/upload, /api/download
      workflows.py  #   /api/workflow/* (start, status, result, rerun, input-to-agent, stream)
      batch.py      #   /api/create-data/* (start, stop, status, result)
      state.py      #   In-memory registries, config, helpers, run wrappers, prune loop.
    main.py         # CLI entry point. Thin — just argument parsing.
    workflow/       # The actual pipeline. Engine, agent factory, resume helpers.
      engine.py     # Single source of truth: runs the 6 stages, writes artifacts.
      agents.py     # Builds the agent set from agents.json.
      resume.py     # Loads cached outputs and coerces uploaded inputs.
      payloads.py   # Builds the JSON each stage receives.
      store.py      # SQLite session/workflow persistence.
    evaluation.py   # Rubric grading, report writing, CSV export.
    artifacts.py    # Reading/writing per-agent input/output JSONL + run metadata.
    pipeline_validation.py  # Deterministic sanity checks (figures, distractor logic, etc.)
    helpers.py      # Prompt loading, metadata merging, small utilities.
    safety.py       # Filename sanitization, safe path joins, timestamps.
    container.py    # OpenAI code-interpreter container lifecycle.
    schemas.py      # The Pydantic shapes for every agent's input and output.
    paths.py        # All file paths used across the backend.
    bootstrap.py    # .env loader.
    tools/          # Standalone dev scripts (style checks, fixture validators).
  data/
    agents.json     # Per-agent model + reasoning settings.
    rubric.json     # Evaluation criteria, grader model.
    dropdown.json   # UI dropdown presets (themes, difficulties).
    prompts/        # One .txt system prompt per agent.
    evaluation/     # Bundled test quizzes for batch runs.
    template.tex    # LaTeX template the latexifier fills in.
  outputs/          # Timestamped run folders. Generated; safe to delete.
  uploads/          # Uploaded files. Generated; safe to delete.
  runtime/          # SQLite workflow store.
  tests/            # Pytest suite.
frontend/
  DESIGN.md                  # UI element mapping: tokens, button/modal system, rules.
  src/
    App.jsx                  # Top-level UI shell (useReducer state).
    components/              # AgentPipeline, AgentCard, ResultsSection, etc.
    components/modals/       # File picker, agent detail, eval results (modals/eval/), etc.
    hooks/                   # useWorkflow (status polling), useAgents, useCreateData, useFileUpload.
    utils/                   # api client, abortableSleep, ids.
    styles/app.css           # All styling; design tokens live in :root.
Other/                       # Eval-only batch scripts and XLSX/comparison tools.
```

## Where each stage's settings live

- **Which model each agent uses + reasoning effort** → `backend/data/agents.json`
- **System prompt for each agent** → `backend/data/prompts/<agent>.txt`
- **Grader model + criteria + scoring rules** → `backend/data/rubric.json`
- **UI dropdown values** → `backend/data/dropdown.json`

These four files are hand-tuned. Editing them changes behavior without touching code.

## Things to know about how runs are organized

- Every run gets a folder under `backend/outputs/<timestamp>-<workflow-id>/`.
- Inside that folder, `agent_outputs/<agent_name>/` holds the agent's `agent_input.jsonl` and `agent_output.jsonl` — one record per attempt, so retries and reruns are preserved.
- `agent_outputs/agent_metadata_output.json` is the index of the run: which agents ran, when, what they consumed, what they produced.
- `final_result.json` is the rolled-up artifact (config + source questions + generated questions + LaTeX/PDF filenames + evaluation summary).
- `evaluation_outputs/` holds the eval report JSON, the JSONL dataset sent to the grader, the agent-level grader outputs, the CSV spreadsheet, and a snapshot of the rubric that was used.
- The compiled LaTeX and PDF sit at the run-folder root.

## Running tests

```bash
pytest backend/tests --ignore=backend/tests/test_safety_store.py
```

The ignored file has a known Windows file-lock flake (a SQLite handle that doesn't release fast enough for tempdir cleanup). It's fine on other platforms.
