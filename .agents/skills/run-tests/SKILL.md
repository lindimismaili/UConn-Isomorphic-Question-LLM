---
name: run-tests
description: Run the full test and lint suite for this repo (backend pytest + ruff, frontend vitest + eslint). Use after any code change before committing, or when the user asks to verify tests pass. Reports pass/fail for each suite and surfaces any failures with context.
---

# Run full test + lint suite

This repo has two independent test suites that must both pass before committing.
Run them together; surface any failures with file + line context.

## Step 1 — Activate the Python environment

```bash
# From repo root. Use the project venv (py3.12) unless another is active.
source .venv/bin/activate 2>/dev/null || true
```

## Step 2 — Backend: pytest + ruff

```bash
# Run from repo root with absolute imports active
python -m pytest -x --tb=short 2>&1
python -m ruff check . 2>&1
```

- `pytest` target: all tests under `backend/` (auto-discovered via `pyproject.toml`).
- `-x` stops on first failure — avoids burying the signal in a long output.
- `ruff check .` enforces line-length 110, target py310 (see `pyproject.toml`).

## Step 3 — Frontend: vitest + eslint

```bash
npm -C frontend test 2>&1
npm -C frontend run lint 2>&1
```

- `vitest run` (single pass, no watch).
- Some pre-existing `react-hooks` warnings in `BatchEvalModal.jsx`,
  `InputToAgentModal.jsx`, and `useBatchEval.js` are not yours — diff to
  confirm you haven't introduced new ones. Lint must report **0 errors**.

## Step 4 — Build check (optional but recommended after CSS/JSX changes)

```bash
npm -C frontend run build 2>&1 | tail -10
```

A clean Vite build (exit 0, `✓ built`) confirms no dead imports or syntax errors
slipped through.

## Reporting results

Report the counts for each suite:

```
Backend pytest:  57/57 passed   ✓
Backend ruff:    clean           ✓
Frontend vitest: 8/8 passed     ✓
Frontend eslint: clean          ✓
Frontend build:  74 modules     ✓
```

If anything fails, quote the failing test name, file, and first error line.
Do not mark the task done until all four suites are clean.

## Notes

- Tests run from **repo root** (`from backend.src.x import ...` requires it).
- There is no root `package.json`; use `npm -C frontend <cmd>`, not `npm run`.
- The backend needs no running server to test — all tests are unit/integration
  with no HTTP calls to the live FastAPI app.
