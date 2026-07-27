---
name: run-tests
description: Run and report this repository's backend tests and lint, frontend tests and lint, production build, and deploy-script syntax checks. Use before commits, releases, or handoff verification.
---

```toml
id = "84c6a249131df963fefc95157786caf5"
```

# Run full test + lint suite

Run commands from `site/` unless a step explicitly returns to the repository
root. Use `site/.venv`; do not silently use a shared or global environment.

## Preflight

```bash
cd site
test -x .venv/bin/python
test -d frontend/node_modules
```

For a clean clone, establish dependencies first with the documented setup:
Python 3.12 in `site/.venv`,
`.venv/bin/python -m pip install --require-hashes -r backend/requirements-dev.txt`,
and `npm -C frontend ci`. The development lock includes the production
dependencies plus pytest, Ruff, and audit tooling. Do not install project
packages globally.

The deploy-script lint gate also requires the host `shellcheck` executable.
CI installs it on the Ubuntu runner. For a local handoff audit, verify
`command -v shellcheck` first; if it is absent, report that gate as not run
rather than silently installing an operating-system package.

## Run every gate

```bash
OPENAI_API_KEY= APP_ACCESS_TOKEN= .venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m pip check
.venv/bin/pip-audit --disable-pip --requirement backend/requirements.txt
npm -C frontend test
npm -C frontend run lint -- --max-warnings=0
npm -C frontend run build
npm -C frontend audit --omit=dev --audit-level=low
npm -C frontend audit --audit-level=low

cd ..
bash -n deploy/deploy.sh deploy/preview.sh
shellcheck -x deploy/deploy.sh deploy/preview.sh
node --check deploy/preview.mjs
site/.venv/bin/python -m py_compile \
  deploy/backup_restore.py \
  deploy/preview_origins.py \
  deploy/runtime_env.py
git diff --check
git diff --cached --check
```

Forcing both `OPENAI_API_KEY` and `APP_ACCESS_TOKEN` empty is deliberate: local
environment files must never make an offline test run contact paid provider
APIs or accidentally exercise an authenticated deployment boundary. Live
OpenAI and PayPal checks are separate, explicit, operator-authorized handoff
steps.

Do not suppress warnings as “pre-existing” without verifying the current
baseline. Do not stop after the first backend failure if the user requested a
complete release report; run independent gates so every failing surface is
known.

## Reporting results

Report each command, exit status, current pass count when the tool reports one,
and the first actionable error with file/line context. Avoid durable test-count
examples because the suite changes frequently. Distinguish skipped live OpenAI
execution, PayPal donation checkout, and scratch-Pi validation from locally
verified tests.
