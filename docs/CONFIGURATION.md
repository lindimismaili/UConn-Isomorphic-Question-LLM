# Configuration

Every environment variable the backend and frontend read, with defaults. Read
this when changing limits, timeouts, cost caps, or the donation recipient, or
before running the site anywhere other than localhost.

[site/.env.example](../site/.env.example) is the maintained non-secret
template; confirm current code before relying on an undocumented variable.

## Where Settings Are Loaded From

| File | Committed? | Purpose |
| --- | --- | --- |
| `site/.env.example` | Yes | Non-secret template documenting every supported variable with blank or commented values. The app never reads it. When you add a variable, add it here and to `site/backend/tests/test_env_example_contract.py`. |
| `site/.env.local` | No, git-ignored | Each machine's private settings, including `OPENAI_API_KEY`. Create it by copying the template. |
| `site/.env` | No, git-ignored | Optional lowest-priority file; you don't need it. Earlier commits tracked it with API keys, which have since been disabled. |

Values load in this order, highest priority first: the process or service
environment, then `site/.env.local`, then `site/.env`. Arbitrary `.env.*.local`
mode files are not loaded.

Create your local file from the repository root:

```bash
cp site/.env.example site/.env.local   # Windows: Copy-Item site\.env.example site\.env.local
chmod 600 site/.env.local              # macOS/Linux: owner-only permissions
```

Raspberry Pi secrets live outside releases in the root-owned, mode-`0600`
`/etc/uconn-isomorphic/environment`, not in any repository file. See
[deploy/HANDOFF.md](../deploy/HANDOFF.md); never commit or
expose real secret values.

## Required

- `OPENAI_API_KEY` - server-only; required for workflow execution, not uploads

## Required Beyond Localhost

- `APP_ACCESS_TOKEN` - shared access token required as the
  `X-App-Access-Token` header on protected API requests; manual values must be
  32-512 characters from the documented secret-safe alphabet
- `APP_RATE_LIMIT_REQUESTS` - in-process cost-route limit, default `12`
- `APP_RATE_LIMIT_WINDOW_SECONDS` - limiter window, default `60`

## Optional Public Configuration

- `PAYPAL_DONATE_URL` - hosted HTTPS `paypal.com`/`paypal.me` donation URL; no
  PayPal API secret is used

## Backend Runtime Settings

Retention, size, timeout, and concurrency settings are strictly bounded;
invalid, zero, negative, non-finite, or out-of-range values stop backend
startup rather than disabling limits or triggering destructive cleanup.

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

## Provider Output Ceilings

Provider output ceilings live in `agents.json` as `maxOutputTokens`: setup
shell tasks are capped at `4096` tokens per response and guardrail classifiers
at `2048`. Current normal-stage per-response caps are Normalizer `2048`,
Extracter/Verifier `32768` each, and Generator/LaTeXifier `65536` each.
Evaluation uses the separate `GRADER_MAX_COMPLETION_TOKENS` setting because it
runs through OpenAI Evals. These are not whole-run token budgets: tool turns
and retries can create additional responses. Per-agent turn caps live in the
same file as `maxTurns` (default `6`, latexifier `12`).

## Frontend Build And Dev Proxy

- `VITE_BACKEND_URL` - backend URL used by the Vite dev server proxy, default `http://127.0.0.1:8000`
- `VITE_PAYPAL_DONATION_EMAIL` - public build-time PayPal recipient used by the
  main header's Donate button; defaults to `Lindim.ismaili0001@gmail.com`

## PayPal Donation Recipient

The main-site header links to PayPal using the confirmed account email in
`site/frontend/src/config/site.js`. To transfer the donation button to a
professor or another maintainer, either:

1. Change `DEFAULT_PAYPAL_DONATION_EMAIL` in that file, or
2. Set `VITE_PAYPAL_DONATION_EMAIL=professor@example.edu` in the frontend build
   environment.

Rebuild the frontend after changing either value. This address is intentionally
public because Vite embeds it in the browser bundle and PayPal donation URL; do
not use it for a secret value.
