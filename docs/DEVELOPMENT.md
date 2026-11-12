# Development

Full local setup, running the backend and frontend separately, tests, prompt
versioning, and development conventions. Read this when you are changing code
rather than only running the app; the [README](../README.md) covers the quick
start.

## Prerequisites

- Python 3.10+ (Python 3.12 is recommended for a fresh local environment; the
  preview launcher accepts 3.10-3.13)
- Node.js 20.19+ within Node 20.x, Node.js 22.13+, or Node.js 24+ and npm.
  Node 22 LTS is recommended for Raspberry Pi deploys; `.nvmrc` pins the
  current local version.
- A professor-owned `OPENAI_API_KEY`, configured server-side

Notes:

- The backend uses OpenAI containers and agents, so the key must be valid for those APIs.
- Hosted-container networking also needs an organization administrator to
  allow the six request-scoped domains in `setup.allowedDomains`:
  `archive.ubuntu.com`, `security.ubuntu.com`, `deb.debian.org`,
  `security.debian.org`, `pypi.org`, and `files.pythonhosted.org`. OpenAI
  requires both the organization allowlist and the request's narrower network
  policy; keep the organization list least-privilege and recheck the current
  dashboard workflow in the
  [official shell-network guide](https://developers.openai.com/api/docs/guides/tools-shell#network-access).
- Normal runs do not require a local LaTeX install. LaTeX compilation happens
  inside the clean downstream OpenAI container.

## Full Development Install

From the repository root, create the canonical environment under `site/` and
install the lockfile exactly.

macOS / Linux:

```bash
cd site
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r backend/requirements-dev.txt
npm -C frontend ci
```

Windows (PowerShell):

```powershell
cd site
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --require-hashes -r backend\requirements-dev.txt
npm -C frontend ci
```

The development lock includes production packages plus pytest, Ruff, and audit
tooling for release verification. A minimal runtime-only install may use
`backend/requirements.txt`; the preview launcher and the Pi deployer do that
automatically. Do not install backend packages into a global or
operating-system Python.

Then copy the committed template `site/.env.example` to the git-ignored
`site/.env.local` and set your key there; see
[Setting An API Key](../README.md#setting-an-api-key) and
[CONFIGURATION.md](CONFIGURATION.md#where-settings-are-loaded-from).

## Running Pieces Individually

The [README](../README.md#running-the-local-preview) covers the all-in-one
`node deploy/preview.mjs --dev` and `--web` launchers. To run parts separately:

Backend API only (`--app-dir site` puts `backend` on the import path):

```bash
site/.venv/bin/python -m uvicorn backend.src.server:app --app-dir site --host 127.0.0.1 --port 8000 --reload
```

Vite dev server in a second terminal:

```bash
npm -C site/frontend run dev:ui
```

The Vite dev server proxies API requests to the local backend. If your backend
is elsewhere, set `VITE_BACKEND_URL` before starting Vite.

Useful frontend commands:

```bash
npm -C site/frontend run build
npm -C site/frontend run lint
npm -C site/frontend run preview
```

## Tests And Lint

Run from `site/` in bash or Git Bash. Backend tests run offline and must not
use a real key:

```bash
cd site
OPENAI_API_KEY= APP_ACCESS_TOKEN= .venv/bin/python -m pytest  # backend
.venv/bin/python -m ruff check .                                # backend lint
npm -C frontend test                                            # frontend (vitest)
npm -C frontend run lint                                        # frontend (eslint)
```

Live workflow tests spend OpenAI funds and are not part of ordinary local
automated tests.

## Prompt Versioning

Prompt templates live in `site/backend/data/prompts/*.txt`. Use Git commits as the
version history for prompt experiments, and keep prompt changes in dedicated
prompt-only commits:

```bash
cd site
.venv/bin/python scripts/prompt_versions.py status
.venv/bin/python scripts/prompt_versions.py snapshot -m "Tune generator distractor instructions"
```

Useful rollback and inspection commands:

```bash
cd site
.venv/bin/python scripts/prompt_versions.py list
.venv/bin/python scripts/prompt_versions.py show <commit> generator
.venv/bin/python scripts/prompt_versions.py restore <commit> generator
```

`restore` creates a new prompt-only commit by default so the rollback itself is
also recorded. Add `--no-commit` only when you need to inspect the restored files
before committing.

## Development Notes

- Do not edit files under `site/backend/outputs/` or `site/backend/uploads/`; they are generated runtime data.
- The backend serves the built frontend from `site/dist/` when available, otherwise it returns a fallback page that points to the dev commands.
- The frontend loads the shared agent registry through the authenticated
  `/api/agents` route; production builds do not publish the backend registry as
  a static asset.
- The UI talks to `/api/upload`, `/api/workflow/start`, `/api/workflow/{id}/status`, `/api/workflow/{id}/result`, and the related workflow endpoints exposed by FastAPI.
- Every provider-cost-bearing start requires a client-generated
  `X-Idempotency-Key` (16-128 characters matching `[A-Za-z0-9._~:-]`).
  The browser creates the key
  before the POST, retains it in tab-local `sessionStorage` after network,
  timeout, 429, or 5xx ambiguity, and reuses it only when the user resubmits
  the same intent; paid POSTs are never retried automatically. The backend
  binds the key to a request fingerprint and original resource response in
  SQLite for at least seven days (and while the original state record is
  retained), so duplicate/concurrent/restarted submissions cannot schedule the
  same work twice. A changed payload with the same key returns HTTP 409.
- Before pushing to `main`, test changes that affect startup, dependencies,
  database/runtime compatibility, uploads, or output artifacts. See
  [OPERATIONS.md](OPERATIONS.md#before-pushing-to-main).

## Repository Skills

Reusable agent workflows for this repository:

- [Agent pipeline changes](../.agents/skills/agent-pipeline-changes/SKILL.md) — safely change or validate the 10-node pipeline and registry metadata.
- [Agent registry checks](../.agents/skills/check-agents-registry/SKILL.md) — validate registry ordering, metadata, filtered sets, and prompts.
- [Grader review](../.agents/skills/grader-review/SKILL.md) — review evaluation artifacts and prepare proposal-only grader improvements.
- [Professor handoff readiness](../.agents/skills/handoff-readiness/SKILL.md) — audit a release candidate for professor handoff.
- [Test runner](../.agents/skills/run-tests/SKILL.md) — run backend and frontend verification.
- [Frontend UI verification](../.agents/skills/verify-frontend-ui/SKILL.md) — verify React chat, stepper, developer tools, and modal surfaces.
