# UConn Isomorphic Question LLM

A 6-stage OpenAI Agents pipeline that takes physics quiz PDFs and produces isomorphic question variants, then grades the output against a rubric.

## Pipeline

```
normalizer → extracter → verifier → generator → latexifier → evaluation
```

Each stage is a typed agent (see [backend/src/schemas.py](backend/src/schemas.py)). The whole orchestration lives in [backend/src/workflow/engine.py](backend/src/workflow/engine.py) — both the FastAPI server and the CLI delegate to it. Don't reintroduce a second copy of the workflow loop. The engine factors the repeated per-stage logic into `resolve_upstream_agent` (the provided/cached resume branches) and `finalize_agent_run` (run-branch completion + artifact write) — reuse them when adding stage logic rather than copy-pasting a stage block.

The frontend tracks progress by **polling** `GET /api/workflow/{id}/status` (the SSE `/stream` endpoint exists but the UI does not use it).

## Run

```bash
# CLI
python backend/src/main.py path/to/quiz.pdf

# Server (app factory in server.py; routes in backend/src/api/)
uvicorn backend.src.server:app --reload

# Frontend dev
cd frontend && npm run dev

# Backend tests
pytest backend/tests --ignore=backend/tests/test_safety_store.py

# Frontend tests + lint
npm -C frontend test
npm -C frontend run lint
```

`test_safety_store.py` has a known Windows file-lock flake; it's excluded by convention until investigated.

## Git Workflow

- After completing a full feature, update, or significant upgrade, create a git commit before finalizing. Stage only the changes from that completed work and use a concise, descriptive commit message that captures the user-facing behavior and major implementation scope.

## Layout

```
backend/
  src/
    workflow/        # engine, agents, resume, payloads, store — single source of truth
    api/             # FastAPI routers by domain: health, config, files, workflows, batch
                     #   + state.py (in-memory registries, config, helpers, run wrappers,
                     #     background pruning loop)
    evaluation.py    # OpenAI Evals integration: rubric grading, report + CSV writing
    artifacts.py     # per-agent input/output JSONL + run metadata read/write
    pipeline_validation.py  # deterministic generator/verifier contract checks
    container.py     # shared OpenAI Code Interpreter container + LaTeX compile
    schemas.py       # Pydantic input/output shapes for every stage
    helpers.py       # prompt loading, metadata merge, small utilities
    safety.py        # filename sanitization, safe path joins, timestamps
    tools/           # one-off scripts: judge_agreement, style_testing, fixture validators
    main.py          # thin CLI
    server.py        # FastAPI app factory: CORS, lifespan (background prune), static mount
    paths.py         # canonical paths used by every module
    bootstrap.py     # dotenv loader + sys.path shim for script-mode execution
  data/
    agents.json      # per-agent model + reasoning config
    rubric.json      # grader model + criteria
    dropdown.json    # frontend theme presets
    prompts/         # one .txt per agent system prompt
  runtime/           # SQLite workflow/batch mirror (workflows.sqlite3)
  tests/
frontend/            # React 19 + Vite UI
  DESIGN.md          # UI element mapping (tokens, buttons, modals) — source of truth
  src/
    utils/           # api client, abortableSleep, ids — shared hook/component helpers
    hooks/           # useWorkflow (status polling), useAgents, useCreateData, useFileUpload
    components/      # AgentPipeline, ResultsSection, modals/ (incl. modals/eval/)
    styles/app.css
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
- **API routers import shared state from `api/state.py`.** Routers hold only route handlers; in-memory registries, config constants, helpers, and the run wrappers live in `state.py`. Don't add a second copy of that state in a router.
- **Agent sequence/labels derive from `artifacts.WORKFLOW_AGENT_SEQUENCE` / `AGENT_DISPLAY_NAMES`.** Don't re-declare the stage order or display names; derive them.
- **Frontend follows [frontend/DESIGN.md](frontend/DESIGN.md).** Reuse `.btn` variants and design tokens; never invent one-off `*-btn` classes or hardcode color/size/motion. Buttons in the same group share one height tier. Use the shared `utils/api.js` client for fetches, not ad-hoc `fetch`.

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
