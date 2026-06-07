# UConn Isomorphic Question LLM

A 6-stage OpenAI Agents pipeline that takes physics quiz PDFs and produces isomorphic question variants, then grades the output against a rubric.

## Pipeline

```
normalizer → extracter → verifier → generator → latexifier → evaluation
```

Each stage is a typed agent (see [backend/src/schemas.py](backend/src/schemas.py)). The whole orchestration lives in [backend/src/workflow/engine.py](backend/src/workflow/engine.py) — both the FastAPI server and the CLI delegate to it. Don't reintroduce a second copy of the workflow loop.

## Run

```bash
# CLI
python backend/src/main.py path/to/quiz.pdf

# Server
uvicorn backend.src.server:app --reload

# Frontend dev
cd frontend && npm run dev

# Tests
pytest backend/tests --ignore=backend/tests/test_safety_store.py
```

`test_safety_store.py` has a known Windows file-lock flake; it's excluded by convention until investigated.

## Git Workflow

- After completing a full feature, update, or significant upgrade, create a git commit before finalizing. Stage only the changes from that completed work and use a concise, descriptive commit message that captures the user-facing behavior and major implementation scope.

## Layout

```
backend/
  src/
    workflow/        # engine, agents, resume, payloads, store — single source of truth
    api/             # FastAPI routes split by domain (workflow, batch, files, admin)
    evaluation/      # rubric, dataset, runner, reporting
    tools/           # one-off scripts: judge_agreement, style_testing, fixture validators
    main.py          # thin CLI
    server.py        # FastAPI app, lifespan, shared helpers
    paths.py         # canonical paths used by every module
    bootstrap.py     # dotenv loader + sys.path shim for script-mode execution
  data/
    agents.json      # per-agent model + reasoning config
    rubric.json      # grader model + criteria
    dropdown.json    # frontend theme presets
    prompts/         # one .txt per agent system prompt
  tests/
frontend/            # React + Vite UI
Other/runEvals.py    # parallel batch evaluator over Phase data
archive/             # user's personal storage — DO NOT IMPORT FROM OR EDIT
```

## Conventions

- **Data files are hand-curated.** Files under [backend/data/](backend/data/) (`*.json`, `prompts/*.txt`, `template.tex`) are managed by the user. Don't delete, rename, or rewrite them. Only edit content when explicitly asked.
- **Agent key is `latexifier`** in all configs and external surfaces (artifact filenames, agent dict keys, agents.json). Internal Python identifiers may still use `latex` for brevity — keep it consistent within a file.
- **Workflow engine never touches HTTP/CLI state directly.** The server passes a `touch_workflow` callback and dict; the engine mutates that dict through the callback. Don't import FastAPI types into `workflow/`.
- **No blocking I/O in async paths.** Filesystem writes inside `workflow/engine.py` must go through the engine's `to_thread` parameter.
- **No module-load side effects.** Don't read JSON/TXT files at module import time. Use lazy accessors with `functools.lru_cache` or first-call loaders.
- **Paths come from `paths.py`.** Don't recompute `REPO_ROOT`, `DATA_DIR`, etc. in each module.
- **No try/except import dance in new files.** The bootstrap shim makes `from backend.src.x import ...` work in both package and script modes.
- **Tests run from repo root.** All test files use absolute imports (`from backend.src.x import ...`).

## Pipeline parallelism

- Within one workflow the 6 stages are sequential (each stage consumes the previous output).
- Multi-file uploads in one workflow are intentionally **combined into one dataset** (extracter prompt handles this).
- The batch endpoint runs workflows concurrently with `Semaphore(BATCH_WORKFLOW_CONCURRENCY=2)`.
- [Other/runEvals.py](Other/runEvals.py) parallelizes per-PDF evaluations with `--concurrency` (default 2).

## Don't do

- Don't add `try/except ImportError` import dances to new files. Use `from backend.src.x import y`.
- Don't import from `archive/`. Nothing references it. It's the user's storage.
- Don't add a "remove this later" comment or feature flag for code you can just remove now.
- Don't reintroduce the old `latex_syntax_quality` rubric criterion — it was removed intentionally.
- Don't mock the database or external state in pipeline_validation tests; integration tests must exercise the real validator.
