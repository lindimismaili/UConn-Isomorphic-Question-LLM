# UConn Isomorphic Question Generator

Generate original, isomorphic physics questions from source PDFs, DOCX files, or text files.
The project uses a multi-agent workflow that normalizes the request, extracts and
verifies source questions, generates new questions, and formats LaTeX. Paid
rubric evaluation is optional and disabled by default.

Professor/operators should begin with
[deploy/PROFESSOR_HANDOFF.md](deploy/PROFESSOR_HANDOFF.md). Do not expose the site
beyond localhost until the server-only API key, shared browser access token,
rate limiting, backup, and release checks in that guide are complete.

## Purpose

The app takes question sources and produces a fresh set of physics questions that preserve the original reasoning pattern while changing the surface details.

The registry shows the full workflow (the Evaluation node is paid, opt-in, and
skipped by default):

`Setup -> Input Guardrail -> Normalizer -> Extracter -> Content Guardrail -> Verifier -> Generator -> Latexifier -> Evaluation -> Output`

Two safety guardrails run inline: an input guardrail (content moderation plus a
jailbreak / prompt-injection check on the request and every uploaded structured
agent-input field, before any container is created) and a content guardrail.
The latter moderates and prompt-injection-screens every string field in the
complete structured extraction, while its academic/physics/is-a-question check
uses only the extracted question records. If a guardrail trips, the run ends
in a `blocked` state with a reason rather than failing as an error.

For coding-agent onboarding, repo conventions, and a full tour of the
architecture, see [AGENTS.md](AGENTS.md).

## Architecture

The repository is split into two runtime surfaces:

- `site/backend/`: FastAPI API, workflow orchestration, evaluation, data files, and run outputs
- `site/frontend/`: React + Vite UI that talks to the backend API
- `site/dist/`: built frontend assets served by the backend when present

After raw-input screening and normalization, an Extracter cache miss gets an
isolated OpenAI container. The backend bootstraps it before uploading source
files, screens the structured extraction, deletes the source container/files,
and only then creates a clean downstream container without source attachments.
Both the server and CLI drive this same engine in
`site/backend/src/workflow/engine.py`.

## Documentation

This is the complete index of maintained repository Markdown. Start with the
section that matches your role; every project documentation file is linked
below.

### Start here

- [README.md](README.md) — this file: setup, environment, run/CLI/output reference, and troubleshooting.
- [AGENTS.md](AGENTS.md) — coding-agent source of truth: conventions, architecture, source-of-truth paths, commands, and data contracts.
- [CLAUDE.md](CLAUDE.md) — Claude Code entry point; a pointer to `AGENTS.md`.

### Professor and deployment operations

- [deploy/PROFESSOR_HANDOFF.md](deploy/PROFESSOR_HANDOFF.md) — professor setup, access, cost controls, Raspberry Pi operations, backup boundaries, limitations, and release checklist.
- [deploy/DEPLOY.md](deploy/DEPLOY.md) — deployment and local-preview operations.
- [deploy/BACKUP_RESTORE.md](deploy/BACKUP_RESTORE.md) — executable Pi backup, phase-journaled restore/recovery, and acceptance-drill runbook.
- [site/SECURITY.md](site/SECURITY.md) — deployment boundary, secret handling, abuse limits, privacy, and vulnerability reporting.

### Application references

- [site/backend/BACKEND.md](site/backend/BACKEND.md) — backend files, functions, workflow, API routes, data, and tests.
- [site/frontend/DESIGN.md](site/frontend/DESIGN.md) — frontend design system, reusable primitives, modal stacking, motion, and design-system showcase. **Read before any UI change.**
- [site/backend/data/calibration/README.md](site/backend/data/calibration/README.md) — evaluation calibration artifact format and maintenance notes.

### History and agent workflows

- [archive/ARCHIVE.md](archive/ARCHIVE.md) — historical GPT Builder, question-source, and report inventory.
- [Agent pipeline changes](.agents/skills/agent-pipeline-changes/SKILL.md) — safely change or validate the 10-node pipeline and registry metadata.
- [Agent registry checks](.agents/skills/check-agents-registry/SKILL.md) — validate registry ordering, metadata, filtered sets, and prompts.
- [Grader review](.agents/skills/grader-review/SKILL.md) — review evaluation artifacts and prepare proposal-only grader improvements.
- [Professor handoff readiness](.agents/skills/handoff-readiness/SKILL.md) — audit a release candidate for professor handoff.
- [Test runner](.agents/skills/run-tests/SKILL.md) — run backend and frontend verification.
- [Frontend UI verification](.agents/skills/verify-frontend-ui/SKILL.md) — verify React chat, stepper, developer tools, and modal surfaces.

## Repository Layout

- `site/backend/src/main.py` command-line workflow runner
- `site/backend/src/server.py` FastAPI app factory (CORS, lifespan, static mount)
- `site/backend/src/api/` HTTP routers by domain (health, config, files, workflows, batch) + `state.py`
- `site/backend/src/workflow/` shared engine, agent factory, payload builders, resume, SQLite store
- `site/backend/src/evaluation.py` OpenAI evals integration
- `site/backend/src/artifacts.py` workflow and agent artifact writers/readers
- `site/backend/src/container.py` explicitly owned OpenAI container runtime helpers
- `site/backend/data/` prompts, rubric, agents config, and evaluation fixtures
  (PDFs in `site/backend/data/evaluation/` are listed dynamically by the UI's
  Browse Test Files and Create Data flows — drop a file in to add an option)
- `site/backend/uploads/` uploaded source files grouped by session
- `site/backend/outputs/` timestamped workflow outputs
- `site/backend/runtime/` SQLite mirror of workflow/batch state
- `site/frontend/src/` React UI (components, hooks, utils, styles)
- `site/frontend/DESIGN.md` UI element mapping (design tokens, button/modal system)

## Prerequisites

- Python 3.10+ (Python 3.12 is recommended for a fresh local environment)
- Node.js 20.19+ within Node 20.x, Node.js 22.13+, or Node.js 24+ and npm.
  Node 22 LTS is recommended for Raspberry Pi deploys.
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

## Setup

From the repository root, create the canonical environment under `site/` and
install the lockfile exactly:

```bash
cd site
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r backend/requirements-dev.txt
npm -C frontend ci
```

The development lock includes production packages plus pytest, Ruff, and audit
tooling for release verification. A minimal runtime-only install may use
`backend/requirements.txt`; the Pi deployer does that automatically. Do not
install backend packages into a global or operating-system Python.

## Environment Variables

The backend loads `site/.env.local` before `site/.env`; existing process/service
environment values have highest precedence. Arbitrary `.env.*.local` mode files
are not loaded. [site/.env.example](site/.env.example) documents the common
runtime variables.

`site/.env` is tracked in this repository by deliberate owner decision. For any
other private value, use the ignored `site/.env.local`. From the repository root
on Unix-like systems, create it with owner-only permissions:

```bash
touch site/.env.local
chmod 600 site/.env.local
```

Raspberry Pi secrets live outside releases in the root-owned, mode-`0600`
`/etc/uconn-isomorphic/environment`. See the handoff guide; never commit or
expose real secret values beyond the tracked file noted above.

Required:

- `OPENAI_API_KEY` - server-only; required for workflow execution, not uploads

Required whenever the site is reachable beyond localhost:

- `APP_ACCESS_TOKEN` - shared access token required as the
  `X-App-Access-Token` header on protected API requests; manual values must be
  32-512 characters from the documented secret-safe alphabet
- `APP_RATE_LIMIT_REQUESTS` - in-process cost-route limit, default `12`
- `APP_RATE_LIMIT_WINDOW_SECONDS` - limiter window, default `60`

Optional public configuration:

- `PAYPAL_DONATE_URL` - hosted HTTPS `paypal.com`/`paypal.me` donation URL; no
  PayPal API secret is used

Backend runtime settings:

- `HOST` - bind host for `python -m backend.src.server`, default `127.0.0.1`
- `PORT` - bind port for the FastAPI app, default `8000`
- Server and CLI entry points use process umask `0077`; mutable
  upload/output/runtime roots are repaired to mode `0700`, and workflow SQLite
  files/sidecars use `0600`
- `MAX_UPLOAD_BYTES` - per-file upload size limit in bytes, default `20971520`
- `MAX_UPLOAD_TOTAL_BYTES` - per-request total upload cap across all files in bytes, default `62914560` (60 MB)
- User instructions/prompts have a fixed 20,000-character ceiling across all
  four paid HTTP start families, CLI starts, and direct engine calls. Oversized
  text is rejected before provider work, idempotency hashing, or persistence.
- `UPLOAD_RETENTION_SECONDS` / `UPLOAD_MAX_BYTES` - automatic upload-session
  cleanup bounds, defaults `86400` (24 hours) and `2147483648` (2 GiB).
  `UPLOAD_MAX_BYTES` is also a live process-wide admission limit: concurrent
  and abandoned sessions are counted before each write, and a full upload
  store returns HTTP 507 rather than waiting for the cleanup pass.
- `OUTPUT_RETENTION_SECONDS` / `OUTPUT_MAX_RUNS` / `OUTPUT_MAX_BYTES` -
  automatic output cleanup bounds, defaults `604800` (7 days), `500` runs, and
  `21474836480` (20 GiB)
- `ARTIFACT_CLEANUP_INTERVAL_SECONDS` - cleanup cadence, default `3600`

Retention, size, timeout, and concurrency settings are strictly bounded;
invalid, zero, negative, non-finite, or out-of-range values stop backend
startup rather than disabling limits or triggering destructive cleanup.
- `WORKFLOW_TTL_SECONDS` - how long workflow records stay in memory, default `86400`
- `WORKFLOW_MAX_ENTRIES` - max workflow/batch records kept in memory, default `200`
- `ACTIVE_WORKFLOW_STALE_SECONDS` - age after which a non-terminal record with
  no live task is marked `lost`, default `21600`
- `PRUNE_INTERVAL_SECONDS` - background prune cadence, default `60`
- `OPENAI_WORKFLOW_CONCURRENCY` - concurrently executing full workflows,
  default `1`
- `MAX_ACTIVE_WORKFLOWS` - admission cap for process-local non-terminal
  workflow records, default `4`; one shared atomic reservation pool covers
  direct starts and every Create Data child so concurrent requests cannot
  over-admit paid work
- `OPENAI_WORKFLOW_BLOCKING_CONCURRENCY` - blocking SDK/filesystem worker bound,
  default `4`
- `OPENAI_AGENT_STAGE_TIMEOUT_SECONDS` / `OPENAI_GUARDRAIL_TIMEOUT_SECONDS` /
  `OPENAI_CONTAINER_TASK_TIMEOUT_SECONDS` / `OPENAI_EVALUATION_TIMEOUT_SECONDS`
  - fail-closed provider deadlines, default `900`, `120`, `600`, and `900`
  seconds; values are strictly bounded
- `OPENAI_GUARDRAIL_STAGE_TIMEOUT_SECONDS` /
  `OPENAI_GUARDRAIL_MAX_CLASSIFIER_REQUESTS` - aggregate wall-clock and call
  budgets for each complete input/content classifier phase, defaults `300`
  seconds and `16` requests; exhaustion blocks without starting another call
- `OPENAI_PROVIDER_REQUEST_TIMEOUT_SECONDS` / `OPENAI_PROVIDER_MAX_RETRIES` -
  sync SDK transport timeout and retry cap, defaults `120` and `0`; disabling
  automatic retries avoids duplicate paid/resource-creation calls
- `WORKFLOW_TOKEN_STREAMING_ENABLED` - legacy token-delta streaming, default
  `false`; the frontend uses status polling
- `BATCH_WORKFLOW_CONCURRENCY` - child-workflow concurrency within one Create
  Data batch, default `2`
- `MAX_CREATE_DATA_ENTRIES` - maximum full generation workflows in one Create
  Data request, default `5`; the UI/API require explicit cost approval
- `OPENAI_STATUS_TIMEOUT_SECONDS` - timeout for the OpenAI status probe, default `5`
- `OPENAI_STATUS_CACHE_SECONDS` - single-flight cache for successful and failed
  provider-status probes, clamped to `5..300`, default `60`
- `OPENAI_CONTAINER_EXPIRES_MINUTES` - owned provider-container lifetime,
  clamped to `1..20`, default `20`
- `OPENAI_CONTAINER_ALLOWED_DOMAINS` - comma-separated allowlist for container
  bootstrap and tools; it cannot grant a domain that the OpenAI organization
  administrator has not also allowed
- `MAX_GENERATED_ARTIFACT_BYTES` - per-file cap for streamed provider-generated
  PDF/TeX downloads, default `52428800` (50 MiB), hard maximum 512 MiB
- `OPENAI_FILE_EXPIRES_SECONDS` - fallback expiry for temporary provider file
  objects if immediate cleanup cannot run, clamped to `3600..2592000`, default
  `86400`
- `GUARDRAILS_ENABLED` - toggle semantic/model input/content guardrail checks
  (overrides the `agents.json` default); structural size/complexity bounds remain
  fail-closed
- `GRADER_MODEL_OVERRIDE` - grade with a different model than the one in `agents.json`
- `GRADER_MAX_COMPLETION_TOKENS` - generated-token ceiling for each of the
  eight score-model grader calls, default `4096`, strict range `256..16384`
- `GENERATION_STRATEGY` - `holistic` (default) or `decompose`
- `GENERATOR_STYLE_REVIEW_ENABLED` - optional second generator wording pass;
  default `false` because it adds another high-reasoning generator call
- `MAX_SOURCE_QUESTIONS` / `MAX_GENERATED_QUESTIONS` /
  `MAX_EVALUATION_ITEMS` - per-run cost-amplification caps, defaults `25`,
  `100`, and `100`
- `MAX_EVAL_BATCH_ENTRIES` / `MAX_EVAL_BATCH_ITEMS` /
  `MAX_CREATE_DATA_ENTRIES` / `MAX_ACTIVE_BATCH_JOBS` - batch admission caps,
  defaults `5`, `100`, `5`, and `1`
- `DOWNLOAD_ZIP_MAX_SOURCE_BYTES` - largest uncompressed output tree accepted
  for on-demand ZIP creation, default `134217728` (128 MiB), hard maximum
  `536870912` (512 MiB); ZIP creation waits for the owning task to finish and
  rebuilds cached archives when the artifact snapshot changes. The browser
  download path buffers the resulting ZIP blob, so raise this limit only for a
  deliberate, well-resourced client.
- `ENABLE_STAGE_CACHE` - exact-match extracter/generator cache; default `false`
- `STAGE_CACHE_TTL_SECONDS` - cache lifetime, default `86400`
- `STAGE_CACHE_MAX_ENTRIES` - maximum cache records, default `128`
- `STAGE_CACHE_MAX_RECORD_BYTES` - maximum serialized record size, default
  `2097152` (2 MiB)
- `OPENAI_AGENTS_DISABLE_TRACING` - SDK tracing is disabled by default; leave
  it disabled for course material unless a reviewed debugging session requires it

Provider output ceilings live in `agents.json` as `maxOutputTokens`: setup
shell tasks are capped at `4096` tokens per response and guardrail classifiers
at `2048`. Current normal-stage per-response caps are Normalizer `2048`,
Extracter/Verifier `32768` each, and Generator/LaTeXifier `65536` each.
Evaluation uses the separate `GRADER_MAX_COMPLETION_TOKENS` setting because it
runs through OpenAI Evals. These are not whole-run token budgets: tool turns
and retries can create additional responses. Per-agent turn caps live in the
same file as `maxTurns` (default `6`, latexifier `12`).
See [site/.env.example](site/.env.example) for the maintained non-secret
template; confirm current code before relying on an undocumented variable.

Frontend dev proxy:

- `VITE_BACKEND_URL` - backend URL used by the Vite dev server proxy, default `http://127.0.0.1:8000`
- `VITE_PAYPAL_DONATION_EMAIL` - public build-time PayPal recipient used by the
  main header's Donate button; defaults to `Lindim.ismaili0001@gmail.com`

### PayPal donation recipient

The main-site header links to PayPal using the confirmed account email in
`site/frontend/src/config/site.js`. To transfer the donation button to a
professor or another maintainer, either:

1. Change `DEFAULT_PAYPAL_DONATION_EMAIL` in that file, or
2. Set `VITE_PAYPAL_DONATION_EMAIL=professor@example.edu` in the frontend build
   environment.

Rebuild the frontend after changing either value. This address is intentionally
public because Vite embeds it in the browser bundle and PayPal donation URL; do
not use it for a secret value.

## Local Server

Run the local backend plus Vite hot-reload UI:

```bash
node deploy/preview.mjs --dev
```

Open:

- the URL printed by the launcher (normally `http://localhost:5173`) for the Vite UI
- that URL plus `/api/health` for a quick backend check through the dev proxy

To run only the backend API (`--app-dir site` puts `backend` on the import path):

```bash
site/.venv/bin/python -m uvicorn backend.src.server:app --app-dir site --host 127.0.0.1 --port 8000 --reload
```

To serve the built frontend from the backend, build `site/dist/` first:

```bash
node deploy/preview.mjs --web
```

## Dev UI

Run the Vite dev server in a second terminal:

```bash
npm -C site/frontend run dev:ui
```

The Vite dev server proxies API requests to the local backend started by `deploy/preview.mjs`.
If your backend is elsewhere, set `VITE_BACKEND_URL` before starting Vite.

Useful frontend commands:

```bash
npm -C site/frontend run build
npm -C site/frontend run lint
npm -C site/frontend run preview
```

## CLI Usage

The CLI drives the shared engine and exposes
argparse subcommands: `run`, `rerun-from-agent`, `from-agent-input`, `batch`,
`eval`, `compare-professor`, `improve`, `show`, `zip`, `list`
(`agents|themes|prompts|rubric|eval-files|runs`), and `prompt`. From `site/`,
run `.venv/bin/python -m backend.src.main --help` (works without `OPENAI_API_KEY`)
for the full list.

The classic flat invocation still works (leading files/flags dispatch to `run`):

```bash
cd site
.venv/bin/python -m backend.src.main "../archive/Question Data Files/PDF/Quiz_Compilation.pdf" -n 2 -i "Make questions harder"
```

`run` flags:

- positional files: one or more supported PDF, document, image, or text inputs
- `-n`, `--num-questions`: number of generated questions per source question
- `-i`, `--instructions`: extra instructions for the workflow
- `--theme`: registry theme label
- `--test-file`: bundled evaluation input; repeatable
- `--stop-after`: stop after a supported agent
- `--eval`: explicitly enable paid rubric evaluation

`run` requires at least one positional source file or `--test-file`; it never
silently chooses a bundled evaluation PDF. Paid evaluation remains off unless
`--eval` is present.

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

## Workflow Stages

The pipeline has 10 registry nodes (6 agent + 2 runtime + 2 guardrail nodes):

1. Own the isolated provider scopes (the displayed setup node; actual
   container creation is lazy and follows the applicable guardrail)
2. Input guardrail: moderation + jailbreak/prompt-injection check on the request
3. Normalize the user request into workflow configuration
4. Extract source questions from the uploaded material
5. Content guardrail: moderation and injection screening over every extracted
   string field, plus an academic/physics/is-a-question check over the question
   records
6. Verify the extracted questions and answers
7. Generate isomorphic questions
8. Format the result as LaTeX and compile a PDF
9. Optionally evaluate the generated output with the paid rubric graders
10. Finalize local output files

If a guardrail trips, the run ends in a `blocked` state (distinct from an error).

The frontend mirrors these stages in the process rail, logs, and result panels.
In Dev mode, clicking a process-rail node opens its detail modal: agents get a
wide input/output inspector, while the runtime nodes open "Starting Runtime"
(runtime parameters plus the run's input files/prompt) and "Ending Runtime"
(inputs plus every output file with view/download). User and Teacher modes keep
the rail status-only. After a run completes, the results panel offers three
modals — Results (config, verified source questions with answers and distractor
logic, generated questions, evaluation summary), Evaluations (per-item judge
results with criterion drill-down), and Agents (run summary, per-agent metadata,
and artifact viewers).

## Outputs

Every workflow writes to `site/backend/outputs/<timestamp>/`.

Common files:

- `final_result.json`
- `isomorphic_questions.tex`
- `isomorphic_questions.pdf`
- `agent_outputs/agent_metadata_output.json`

Agent artifacts:

- `agent_outputs/<agent>/agent_input.jsonl`
- `agent_outputs/<agent>/agent_output.jsonl`

Evaluation artifacts:

- `evaluation_outputs/evaluation_report.json`
- `evaluation_outputs/evaluation_dataset.jsonl`
- `evaluation_outputs/evaluation_agents.jsonl`

Batch create-data runs also write batch summaries and per-file workflow folders under `site/backend/outputs/`.

## Evaluation Artifacts

The evaluation step uses the rubric in `site/backend/data/rubric.json` and writes scored results into the output folder for the run.
Those files are the primary record of how the generated questions were judged.

Evaluation is paid and opt-in for ordinary web and CLI generation. The rubric
uses eight score-model graders, so evaluating `N` source/generated pairs
creates approximately `8 × N` grader calls in addition to workflow costs.
The web API requires a separate cost-confirmation flag before any evaluation
start. Upload-mode Eval Batch also treats each source/generated PDF as
untrusted: `F` PDFs use `F` isolated extraction containers, up to `2 × F`
Extracter calls when every file needs its one retry, and `F` document-bootstrap
calls. With the default five generated entries, `F` is at most six. The API and
UI project and require approval for these calls as well as the graders. The
moderation/content classifiers also run for every extracted PDF and may batch
into additional calls. The default second generator style-review pass is also
off.

The node taxonomy is architectural, not a billing count. A normal full
generation run with evaluation disabled still makes model-backed calls for five
core agent stages, three classifier calls across the two guardrail nodes
(input injection, content injection, and academic/content classification), and
the container bootstrap/LaTeX compile shell orchestration (at least ten
model-backed calls when each guardrail fits in one batch), plus moderation,
tool turns, container, and file usage. Retries and additional guardrail batches
increase that total. Use the OpenAI project usage limits as the financial
backstop only after verifying the dashboard's current spend-enforcement
behavior, and configure alerts as well. The application's request limits are
abuse controls, not a dollar budget.

A cache-miss full run uses two sequential provider-container sessions: an
extraction-only scope and, after the content guardrail accepts and the source
scope is deleted, a fresh downstream scope with no source file IDs. This adds
no extra agent or shell-model call beyond the counts above, but current
container-time/file charges still apply. An Extracter cache hit skips the
source upload, document bootstrap, and extraction container entirely.

The OpenAI Evals API used here becomes read-only on **October 31, 2026** and
shuts down on **November 30, 2026**, per the
[official OpenAI deprecations page](https://developers.openai.com/api/docs/deprecations).
A replacement evaluation path must be implemented and verified before those
dates.

## Troubleshooting

- `OPENAI_API_KEY` missing: the backend API can start, but workflow execution will fail until the key is set.
- `site/dist/index.html` missing: run `npm -C site/frontend run build`, then restart the backend.
- `crypto.hash is not a function` or Vite rejects the Node version: use Node
  20.19+ within Node 20.x, Node 22.13+, or Node 24+. On Raspberry Pi, run
  `bash deploy/deploy.sh`; the deployer provisions Node 22 when needed.
- Upload rejected: check the file extension and `MAX_UPLOAD_BYTES`.
- Workflow appears stuck: check `/api/health`, `/api/openai/status`, and the latest folder in `site/backend/outputs/`.
- Frontend cannot reach the backend in dev mode: run
  `node deploy/preview.mjs --dev` and open the URL it prints (normally
  `http://localhost:5173`).

## Raspberry Pi Automatic Deployment

Use a supported **64-bit Raspberry Pi OS** installation; verify the device
reports an `aarch64` kernel and `arm64` packages. The preflight requires
Python 3.10+, at least 1,700 MiB RAM (a 4 GB-class Pi is recommended), and at
least 8 GiB free on both the release and persistent-data filesystems. Run
`bash deploy/deploy.sh --preflight` before installation. The first deployment
should remain local. Do not enable public Cloudflare access until
`APP_ACCESS_TOKEN`, header rejection/acceptance, rate limiting, download
protection, a manual backup, Cloudflare Access, and an HTTP-to-HTTPS redirect
have been verified.

The Pi deployer treats the root-configured, pinned GitHub remote's `main`
branch as the only automatic deployment source. It fetches that ref without
merging, builds the commit in an isolated release directory, then atomically
changes the `current` release symlink only after the candidate is ready. The
prior three known-good releases remain on disk for rollback.

- User data survives every deployment under the persistent `uploads/`,
  `outputs/`, and `runtime/` directories. This includes generated questions,
  evaluations, uploaded files, and workflow-history SQLite state.
- A failed fetch, build, restart, or health check leaves the existing release
  running or restores it automatically. The failed commit is quarantined until
  the pinned `main` ref changes, so the default 30-minute timer (plus up to five
  minutes of jitter) does not rebuild a known-bad commit forever.
- `GET /api/deployment/status` exposes a sanitized summary and recent bounded
  deployment events for the operations dashboard. Detailed diagnostics remain
  in `journalctl -u uconn-isomorphic-update.service`.
- Device secrets are stored outside releases in the root-owned
  `/etc/uconn-isomorphic/environment`; release `.env` files contain only
  non-secret defaults.
- Backups are operator-triggered through the root-owned
  `uconn-isomorphic-backup-restore` helper. It captures persistent SQLite data,
  uploads, outputs, deployment state, and secrets only onto a separately
  mounted Linux filesystem with a verified LUKS dm-crypt ancestor. Schema-2
  archives include a verified bundle of the exact Git commit and reachable
  history. Restore requires the archive/checksum to stay together on equivalent
  verified LUKS storage, rotates the browser token, and retains marker-bound
  pre-restore roots until the operator explicitly finalizes the accepted
  transaction; immutable release rollback remains code-only.
- On an installed Pi, every web/CLI paid start holds a shared lease on the
  root-owned `/run/uconn-isomorphic-work-lock/activity.lock` through provider
  and artifact cleanup. A systemd-tmpfiles rule recreates its exact root-owned
  directory/file ABI after reboot without replacing a safe live inode. Deploy,
  backup, and restore publish maintenance, take the conflicting exclusive
  lease, and keep it through restart, health verification, and maintenance
  removal, preventing a race between accepted paid work and quiescence.
- The installer also enables the minute-by-minute operations probe, which feeds
  sanitized Pi, tunnel, disk, public-health, and Cloudflare status into `/admin`.

## Secure Operations Dashboard

`/admin` is a protected operations dashboard for archive, uploads, real output
folders, evaluation artifacts, selected prompts/configuration files, workflow
history, maintenance/cache controls, health reports, and incident/audit history.
Its Overview refreshes every 30 seconds without reloading the page and reports
site reachability, Cloudflare/tunnel state, OpenAI status, active visitors,
workflows, storage, and incidents. Configure Cloudflare Access to protect
`/admin*` and `/api/admin/*` with Google sign-in, then set
`CF_ACCESS_TEAM_DOMAIN`, `CF_ACCESS_AUD`, and a comma-separated `ADMIN_EMAILS`
allowlist only on the Pi. The server validates the signed Access assertion
itself; a Cloudflare page rule alone is not sufficient.

The file browser displays canonical logical paths, such as
`/site/backend/outputs`. Its `Prompts & configuration` root exposes only
`data/prompts/*.txt`, `data/agents.json`, `data/latex_template.tex`, and
`data/rubric.json`; it does not expose arbitrary data files, runtime databases,
or credentials.

### Local dashboard testing

The tracked `site/.env` enables a non-production dashboard test identity so a
fresh repository checkout can exercise the full dashboard without Cloudflare:

```env
ADMIN_LOCAL_DEV_MODE=true
ADMIN_LOCAL_DEV_EMAIL=local-admin@example.test
```

Start the site bound to `127.0.0.1`/`localhost`, then visit
`http://localhost:8000/admin`. This bypass is accepted only from a direct
loopback connection and only while both variables are set; it must never be
placed in the Pi runtime configuration. It exercises the real local file,
workflow, maintenance, cache, and download controls.

`site/.env.local` can still override the local test identity when needed, but
it is not required. The Pi systemd service explicitly sets
`ADMIN_LOCAL_DEV_MODE=false` after loading every environment file, so the
checked-in local default can never bypass Cloudflare Access in production.

The dashboard stores non-secret state in `runtime/operations.sqlite3`. The Pi
installer creates a release-independent `admin-runtime.env` override for an
admin-replaced `OPENAI_API_KEY` and optional `OPENAI_ADMIN_KEY`; neither is
returned through the API. Optional SMTP alert settings are `SMTP_HOST`,
`SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, and
`ALERT_RECIPIENTS`. The operations timer writes a sanitized Pi/cloudflared
report every minute. The tunnel installer does not create a Cloudflare Access
application or Google policy; configure those in Cloudflare Zero Trust after
the public hostname is available.

Before pushing to `main`, test changes that affect startup, dependencies,
database/runtime compatibility, uploads, or output artifacts. Runtime-data
changes must be backward compatible with the prior release or include an
explicit migration and rollback path; automatic deployment can roll back code,
but it cannot safely undo destructive data migrations.

## Current Limitations

- `APP_ACCESS_TOKEN` is one shared gate, not student accounts or role-based
  access control.
- The request limiter is in-process and resets on backend restart. It is not a
  per-student quota, dollar ledger, or semester budget.
- Temporary provider files have a crash-safe expiry, but a hard process or
  device failure can still leave remote evaluation definitions/runs for manual
  provider cleanup.
- User/Teacher/Dev are presentation modes, not authorization.
- The Dev panel is a workflow/developer surface, not a full host-admin
  dashboard. The proposed professor admin dashboard is not implemented:
  models, prompts, secrets, systemd, Cloudflare, backups, and budgets are not
  safely mutable from the UI.
- Uploaded document contents reach the tool-enabled Extracter before the
  post-extraction content guardrail. Restrict uploads to trusted course
  material; public anonymous uploads need a bounded local parsing/OCR
  quarantine stage that is not implemented yet.
- Hosted extraction/LaTeX containers install allowlisted runtime tooling on
  demand. Direct Python document packages are version-pinned, but the
  ephemeral provider environment does not have the Pi release's complete
  transitive hash lock.
- OpenAI Eval Batch Stop is cooperative and best-effort; an already-running
  remote evaluation call may finish after Stop is requested.
- Backups and restore are operator-run, deployment-locked procedures; there is
  no unattended backup schedule. A physical spare-SD restore drill is still
  required before handoff.
- Persistent Pi course data is permission-restricted but not encrypted at rest
  by this repository; physically secure the device/SD card or provision
  encrypted storage for sensitive or student-identifiable material.
- No software license has been selected in this repository; the owner must make
  that decision before external redistribution.
- Live workflow tests spend OpenAI funds and are not part of ordinary local
  automated tests.

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
