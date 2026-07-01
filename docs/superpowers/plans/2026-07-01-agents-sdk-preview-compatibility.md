# Agents SDK Preview Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure local preview installs an Agents SDK version that exports every exception imported by the backend.

**Architecture:** Keep the existing conditional dependency installation flow. Encode the required SDK floor in `requirements.txt`, and extend the preview import probe so an existing but incompatible environment enters the normal requirements installation path.

**Tech Stack:** Bash, Python 3.12, pytest, `openai-agents`

---

### Task 1: Add dependency compatibility regression coverage

**Files:**
- Create: `site/backend/tests/test_preview_dependencies.py`

- [x] **Step 1: Write the failing tests**

```python
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[3]


def test_agents_sdk_requirement_includes_model_refusal_error_release() -> None:
    requirements = (REPO_ROOT / "site/backend/requirements.txt").read_text()
    assert "openai-agents>=0.15.0" in requirements.splitlines()


def test_preview_dependency_probe_checks_agents_sdk_exception() -> None:
    preview_script = (REPO_ROOT / "deploy/preview.sh").read_text()
    assert "from agents.exceptions import ModelRefusalError" in preview_script
```

- [x] **Step 2: Run the focused test and verify RED**

Run from `site/`:

```bash
../.venv/bin/python -m pytest backend/tests/test_preview_dependencies.py -q
```

Expected: two assertion failures because the requirement is currently `openai-agents>=0.0.1` and the preview probe only imports `fastapi, uvicorn`.

### Task 2: Enforce the compatible SDK during preview startup

**Files:**
- Modify: `site/backend/requirements.txt:11`
- Modify: `deploy/preview.sh:97-100`

- [x] **Step 1: Raise the Agents SDK dependency floor**

Replace the existing requirement with:

```text
openai-agents>=0.15.0
```

- [x] **Step 2: Extend the preview dependency probe**

Replace the existing probe condition with:

```bash
if ! "${PYTHON_BIN}" -c "import fastapi, uvicorn; from agents.exceptions import ModelRefusalError" >/dev/null 2>&1; then
```

Keep the existing `pip install -r` body unchanged so failed probes use the established installation path.

- [x] **Step 3: Run the focused test and verify GREEN**

Run from `site/`:

```bash
../.venv/bin/python -m pytest backend/tests/test_preview_dependencies.py -q
```

Expected: `2 passed`.

- [x] **Step 4: Update the active preview environment**

Run from the repository root:

```bash
.venv/bin/python -m pip install -r site/backend/requirements.txt
```

Expected: `openai-agents` is upgraded to at least 0.15.0 and the command exits zero.

- [x] **Step 5: Verify the original import path**

Run from `site/`:

```bash
../.venv/bin/python -c "from agents.exceptions import ModelRefusalError; import backend.src.server"
```

Expected: exit zero with no traceback.

### Task 3: Verify and checkpoint the fix

**Files:**
- Modify: `docs/superpowers/plans/2026-07-01-agents-sdk-preview-compatibility.md` (checkbox status only)

- [x] **Step 1: Run the full backend and frontend test/lint suites**

Run from `site/`:

```bash
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
npm -C frontend test
npm -C frontend run lint
```

Expected: all four commands exit zero.

- [x] **Step 2: Smoke-test preview backend health**

Run the preview task, wait for startup, request `/api/health`, and stop the task:

```bash
bash deploy/preview.sh --dev
curl -fsS http://127.0.0.1:8000/api/health
```

Expected: backend starts without an import traceback and health returns a successful JSON response.

- [x] **Step 3: Inspect and commit only the compatibility fix**

```bash
git status --short
git diff --check
git diff -- site/backend/requirements.txt deploy/preview.sh site/backend/tests/test_preview_dependencies.py docs/superpowers/plans/2026-07-01-agents-sdk-preview-compatibility.md
git add -- site/backend/requirements.txt deploy/preview.sh site/backend/tests/test_preview_dependencies.py docs/superpowers/plans/2026-07-01-agents-sdk-preview-compatibility.md
git commit -m "fix: sync preview Agents SDK dependency"
```

Expected: a path-scoped commit on the current branch, with no unrelated files staged.
