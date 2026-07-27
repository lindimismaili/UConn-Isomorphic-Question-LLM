# Backend Reference — `site/backend/`

The FastAPI + OpenAI Agents SDK backend that powers the Isomorphic Question
Generator. It exposes an HTTP API (and a CLI) that drives a 10-node agent
pipeline (6 agent + 2 runtime + 2 guardrail registry nodes): read uploaded physics
questions, verify them, generate isomorphic variants, render a PDF, and grade
the result — with two safety guardrails inline.

This file documents **the key backend modules, important functions, and how the
workflow is structured**. Repo-wide conventions and the data
contracts live in [../../AGENTS.md](../../AGENTS.md); the frontend is documented
in [../frontend/DESIGN.md](../frontend/DESIGN.md).

> **Runs from `site/`.** The backend resolves its repo root as `site/`
> (`paths.REPO_ROOT` = the parent of `backend/`), so `.env`, `dist/`, and the
> venv live under `site/`. Launch with `uvicorn backend.src.server:app` (or
> `--app-dir site` from the repo root). See AGENTS.md → *Commands That Work*.

---

## Directory layout

```
site/backend/
├── BACKEND.md              # this file
├── requirements.txt        # Python deps
├── data/                   # hand-curated config + prompts (source of truth)
│   ├── agents.json         # the 10 nodes + _config (themes, api title/desc, guardrails)
│   ├── rubric.json         # 8 evaluation criteria (pass/fail, per-criterion reasoning)
│   ├── latex_template.tex  # LaTeX preamble/macros injected into the latexifier
│   ├── prompts/*.txt        # one prompt per agent + guardrail classifiers + container bootstrap tasks
│   ├── calibration/         # versioned meta-evaluator calibration snapshots (*.jsonl)
│   ├── evaluation/          # bundled source PDFs for the Create Data batch flow
│   └── testing/             # saved agent-input fixtures + source/prompt samples
├── src/
│   ├── server.py  main.py  cli_agents.py     # entry points
│   ├── paths.py  bootstrap.py  safety.py  auth.py  # foundation + access boundary
│   ├── cost_controls.py  provider_{clients,timeouts}.py  deployment_maintenance.py
│   ├── registry.py  schemas.py  helpers.py    # config gateway, types, utils
│   ├── artifacts.py  container.py             # output I/O + OpenAI container
│   ├── evaluation.py  eval_pipeline.py  pipeline_validation.py
│   ├── api/            # FastAPI routers + shared state
│   ├── workflow/       # the engine, agent factory, payloads, resume, stores, guardrails
│   └── tools/          # developer/analysis CLIs (not on the request path)
├── outputs/            # per-run artifacts, grouped <mm-dd-yyyy>/<hh-mm-ss> (gitignored contents)
├── uploads/            # random-capability session directories (gitignored contents)
└── runtime/            # SQLite stores (gitignored contents)
```

---

## The 10-node pipeline

One workflow runs the nodes **in order**; each consumes the previous output.
`setup` and `output` are `kind: "runtime"`; `input_guardrail` and
`content_guardrail` are `kind: "guardrail"`; the middle six are agent nodes.
These kinds describe orchestration, not cost: the guardrail classifiers and
the setup/compile shell tasks are model-backed, while `output` is local. The
engine ([workflow/engine.py](src/workflow/engine.py)) drives them via
`run_workflow_async()`.

| # | Node | Type | What it does |
|---|---|---|---|
| 0 | `setup` | runtime | Own the provider-resource lifecycle. G2 and Normalizer run before any container. An Extracter cache miss gets an extraction-only container; document bootstrap finishes before source upload/attachment. After G3 accepts, that container and the provider files are deleted before a fresh downstream container is created with `file_ids=[]`. |
| 0.5 | `input_guardrail` | guardrail | OpenAI Moderation + a blocking jailbreak/prompt-injection classifier on the instruction and every string in an uploaded structured agent input, before any container. |
| 1 | `normalizer` | agent | Parse the user prompt into a structured config (count, difficulty, theme, title, notes, show-answers). |
| 2 | `extracter` | agent | Read uploaded files and extract source questions (stem, options, type, figure-dependence, grouping). Retries once if zero questions. |
| 2.5 | `content_guardrail` | guardrail | Moderation and prompt-injection screening over every string in the complete Extracter output; the separate academic/physics/on-topic/question classifier receives only extracted question records. |
| 3 | `verifier` | agent | Solve/validate answers, fill per-option explanations + `distractor_logic`, and emit a per-question `classification`. |
| 4 | `generator` | agent | Produce isomorphic questions (holistic or decompose strategy), preserving concept/structure/distractor roles. |
| 5 | `latexifier` | agent | Render the questions as LaTeX and compile a PDF inside the clean downstream container; the model-backed compile shell call lazily installs TeX only if it is absent. |
| 6 | `evaluation` | agent | When explicitly enabled, run the paid OpenAI Evals rubric (8 criteria) over source↔generated pairs; write the report + spreadsheet. |
| 7 | `output` | runtime | Write `final_result.json`, `agent_outputs/` artifacts, and the downloadable file manifest. |

Guardrail checks execute inline; their registry nodes are **excluded from the
runnable/stoppable/LLM sets** (`registry.input_to_agent_sequence()`,
`llm_agents()`, and `stoppable_agents()` skip `kind == "guardrail"`). A detected
violation ends in terminal **`blocked`** status (distinct from `error`). Current
`failClosed: true` means detected violations and guardrail infrastructure
failures block as a precaution; the engine records the terminal reason as
`workflow["blocked"] = {category, reason, stage}`.
See [workflow/guardrails.py](src/workflow/guardrails.py).

**Stage execution & hardening.** The five normal Agents SDK stages (Normalizer
through LaTeXifier) run through `run_llm_stage()`. It streams only when an
`emit_token` callback is supplied; otherwise it uses `Runner.run`, and catches
`MaxTurnsExceeded` / `ModelBehaviorError` / `ModelRefusalError` → a clean
per-stage `ValueError`. Evaluation uses the Evals integration, guardrails use
their own bounded classifier calls, and runtime nodes are orchestration. Each
normal stage gets an explicit `max_turns` (optional `agents.json` `maxTurns`;
default 6, latexifier 12) plus its per-response registry `maxOutputTokens`
limit. Current normal-stage values are 2048 (Normalizer), 32768
(Extracter/Verifier), and 65536 (Generator/LaTeXifier). Setup shell responses
are capped at 4096 tokens, guardrail classifier responses at 2048, and Evals
graders use `GRADER_MAX_COMPLETION_TOKENS`. Tool turns and retries may produce
additional responses, so no one cap is a whole-run token budget. Each explicitly
owned container has a cancellation-drained heartbeat; latexifier
recreate-on-expiry creates only a downstream replacement and never reattaches
source file IDs.

**Resume / partial runs.** The six `kind: "agent"` nodes
(`normalizer`, `extracter`, `verifier`, `generator`, `latexifier`,
`evaluation`) are the independently selectable starting points. Evaluation
still requires explicit evaluation opt-in. The engine skips upstream stages and
rebuilds their context either from a previously cached run
(`rerun-from-agent`) or from an uploaded payload (`start-from-agent-input`). It
can also stop early (`stop_after_agent`) and skip evaluation
(`run_evaluation=False`). Raw instructions and resolved Extracter content are
guardrailed again on fresh, cached-replay, and uploaded-input paths, so altered
replay material cannot skip the applicable checks.

**Engine shared helpers** (reused by every stage instead of copy-pasting):
- `finalize_agent_run(...)` — mark a freshly-run stage completed, mirror its output into the live workflow dict (for the UI), and persist input/output/metadata artifacts.
- `resolve_upstream_agent(...)` — reconstruct a skipped stage from upload (`from_input`) or cache (`from_cache`), applying the same post-processing and recording "provided"/"reused" state.
- `copy_or_record_reused_agent_artifact(...)`, `mark_provided_agent_state(...)`, `mark_reused_agent_state(...)`, `record_provided_agent_artifact(...)` — state/artifact bookkeeping for the resume paths.
- `materialize_latex_output(...)` — write the `.tex` safely, compile to PDF in the clean downstream container, return canonical paths.
- `file_metadata(...)` — API-facing file descriptor (id, size, mediaType, downloadable).
- `_WorkflowContainerManager` — creates only explicit, ledger-owned containers;
  builds extraction-only versus downstream-only agent subsets; drains bootstrap
  and heartbeat tasks; and fails closed if the untrusted source scope cannot be
  retired before downstream work.

The engine takes callbacks (`touch_workflow`, `to_thread`, `load_rubric_model`,
`load_rubric_payload`) so it never imports FastAPI/CLI state — the same engine
serves the server and the CLI.

---

## Source modules

### Entry points

| File | Run with | Purpose |
|---|---|---|
| [src/server.py](src/server.py) | `uvicorn backend.src.server:app` | FastAPI app factory. `lifespan()` reconciles persisted records, starts `pruning_loop()`, and terminalizes/cancels tracked work on shutdown; registers the domain routers (health, config, files, workflows, batch, eval_batch), CORS, and a static mount that serves `site/dist/` (falls back to an HTML notice if `index.html` is absent). Direct `python -m backend.src.server` launch defaults to `127.0.0.1`; `app` is built at import. |
| [src/main.py](src/main.py) | `python -m backend.src.main <cmd> [opts]` | CLI workflow orchestrator over the shared engine. Argparse subcommands: `run`, `rerun-from-agent`, `from-agent-input`, `batch`, `eval`, `compare-professor`, `improve`, `show`, `zip`, `list` (`agents\|themes\|prompts\|rubric\|eval-files\|runs`), `prompt`. Backward-compatible: leading files/flags dispatch to `run`. `run` requires a source file or explicit `--test-file`; it never silently chooses a bundled fixture. OpenAI client is created lazily so `--help` needs no key; paid evaluation on ordinary generation requires `run --eval`. |
| [src/cli_agents.py](src/cli_agents.py) | `python -m backend.src.cli_agents <cmd>` | Per-agent CLI for isolated testing: `list`, `schema <agent>`, `validate <agent> --input`, `run <agent> --input`, `run-one <agent> --input`. |

### Foundation

- **[src/paths.py](src/paths.py)** — canonical filesystem paths computed once at import (no I/O). `REPO_ROOT` = parent of `backend/` (i.e. `site/`); exports `ENV_PATH`, `DATA_DIR`, `PROMPTS_DIR`, `AGENTS_CONFIG_PATH`, `RUBRIC_PATH`, `LATEX_TEMPLATE_PATH`, `OUTPUTS_DIR`, `UPLOADS_DIR`, `RUNTIME_DIR`, `DIST_DIR`, `VERSION_PATHS`. **Import paths from here — never recompute `__file__` chains.**
- **[src/bootstrap.py](src/bootstrap.py)** — environment loading and owner-only process umask for the server and CLIs. Ignored `site/.env.local` loads before an optional ignored `site/.env`; existing process/systemd values win. Arbitrary `.env.*.local` files are not loaded.
- **[src/auth.py](src/auth.py)** — optional shared-token boundary. When `APP_ACCESS_TOKEN` is configured, protected API requests require `X-App-Access-Token`; unsafe browser API methods with an `Origin` header must exactly match the same `ALLOWED_ORIGINS` list used by CORS. Cost-bearing POSTs use per-operation in-process IP limits (`APP_RATE_LIMIT_REQUESTS=12`, `APP_RATE_LIMIT_WINDOW_SECONDS=60` defaults), while uploads and on-demand ZIP downloads use a separate four-times resource allowance. Also validates optional HTTPS PayPal donation URLs.
- **[src/cost_controls.py](src/cost_controls.py)** — strict, fail-closed
  amplification bounds for source/generated/evaluated questions, eval/Create
  Data batches, active batches, each grader's completion tokens, browser-ZIP
  source size, and the optional second generator style-review pass. The ZIP
  default is 128 MiB (512 MiB hard maximum) because the browser buffers the
  resulting blob. Invalid environment overrides raise instead of silently
  removing a cost boundary.
- **[src/deployment_maintenance.py](src/deployment_maintenance.py)** — installed
  Pi admission boundary for paid work. Web/CLI starts take and retain a shared
  lease on the root-owned activity lock through provider and artifact cleanup;
  deploy/backup/restore take the conflicting exclusive lease before entering
  maintenance. A deployed host fails closed if the lock path or metadata is
  missing/unsafe.
- **[src/provider_timeouts.py](src/provider_timeouts.py)** — strict finite
  provider deadlines shared by the server and CLIs. Async agent/guardrail
  attempts fail closed, each complete input/content classifier phase has
  aggregate wall-clock and request-count budgets, container shell/tool
  operations are bounded, eval polling has a bounded wait, and explicitly
  constructed sync SDK clients use a finite transport timeout with automatic
  retries off by default.
- **[src/provider_clients.py](src/provider_clients.py)** — the
  application-owned Agents SDK `AsyncOpenAI` client/provider. It applies the
  same finite transport timeout and retry policy as the synchronous
  Files/Containers/Evals client, serializes first initialization, detects a key
  change without retaining a second plaintext copy, requires a service restart
  for key rotation, and closes/reset the provider during graceful shutdown.
- **[src/safety.py](src/safety.py)** — shared input and filesystem/artifact
  safety. `validate_instruction_text()` strips and enforces the fixed
  20,000-character instruction ceiling; paid HTTP/CLI starts call it before
  provider work, idempotency hashing, or persistence, and the engine repeats
  the check for direct callers. Filesystem helpers include
  `safe_filename()`/`safe_stem()` (sanitize), `ensure_child_path()`
  (path-traversal guard), `create_unique_run_dir()`/`unique_child_name()`
  (collision-free output dirs), `stable_short_hash()` (deterministic short
  hashes for IDs/cache keys), `write_json()`, and
  `utc_run_timestamp()`/`utc_now_iso()`.
- **[src/registry.py](src/registry.py)** — **lazy, validated gateway to `agents.json`.** `_load_raw()` is `@lru_cache`'d and fails fast unless the exact ten-node engine contract, unique sequences/aliases, required metadata, kind invariants, output-token bounds, and guardrail references are valid. Accessors: `agent_sequence()`, `all_agents()`, `agent_entry(key)`, filtered views `input_to_agent_sequence()`/`llm_agents()`/`stoppable_agents()`/`source_file_agents()`/`promptable_agents()` (`input_to_agent_sequence()` contains only independently runnable agent stages), naming `display_names()`/`cli_descriptions()`/`count_labels()`/`aliases_map()`, setup/container `setup_bootstrap_model()`/`setup_max_output_tokens()`/`setup_allowed_domains()`/`setup_container_packages()`, guardrail config `guardrails_enabled()`/`guardrail_model()`/`guardrail_moderation_model()`/`guardrail_reasoning()`/`guardrail_max_output_tokens()`/`guardrail_physics_min_confidence()`/`guardrail_block_off_topic()`/`guardrail_fail_closed()`/`guardrail_max_questions_sampled()`/`guardrail_input_node()`/`guardrail_content_node()`/`guardrail_nodes()`, plus `evaluation_model()`/`evaluation_agent()`, `themes()`, `api_title()`, `api_description()`, `default_from_agent()`. **Don't hardcode registry-owned collections — import them from here.** In tests that swap `AGENTS_CONFIG_PATH`, call `registry._load_raw.cache_clear()`.
- **[src/schemas.py](src/schemas.py)** — all Pydantic models. Question structure: `OptionItem`, `QuestionPart`, `SourceQuestionItem`, `NewQuestionItem`, `QuestionClassification` (verifier-derived profile), `QuestionDecomposition` (decompose-strategy audit). Per-stage outputs: `NormalizerOutput`, `ExtracterOutput`, `VerifierOutput`, `GeneratorOutput`, `LaTeXOutput`. End-to-end `WorkflowResult`. `AGENT_OUTPUT_TYPES`/`AGENT_INPUT_TYPES` map agent keys → models (used as the Agents SDK `output_type`).
- **[src/helpers.py](src/helpers.py)** — shared utils: strict required-prompt loading `resolve_prompt_path()`/`load_prompt()` and validated `render_prompt()` placeholder substitution, `upload_file_to_openai()`, `build_json_message()` (wrap JSON + file refs as an OpenAI user message), `apply_requested_difficulty()`, `merge_extracted_metadata_into_config()`, `normalize_question_types()`.

### Workflow engine — `src/workflow/`

- **[engine.py](src/workflow/engine.py)** — `run_workflow_async()` orchestrates the 10 nodes and the shared helpers described above. G2 and Normalizer run before any container. An Extracter cache miss creates a ledger-owned extraction-only scope, completes document bootstrap, then uploads/attaches source files. After G3 accepts, fresh output may be cached/persisted; the engine must delete the source container and provider files before it creates a downstream-only container with `file_ids=[]`. Cache hits and replayed structured inputs skip the source scope but still pass G3. Agent calls run inside a privacy-disabled `trace()` context and through `run_llm_stage()` (streaming/`max_turns`/typed-error handling plus a fail-closed `OPENAI_AGENT_STAGE_TIMEOUT_SECONDS` deadline). A guardrail trip ends in terminal `blocked`; failed source cleanup prevents downstream work. Repair sub-runs get a `custom_span`; the second style-review call is off unless `GENERATOR_STYLE_REVIEW_ENABLED=true`; blocking cache/eval filesystem I/O uses `to_thread`.
- **[guardrails.py](src/workflow/guardrails.py)** — the three safety checks:
  **G1** OpenAI Moderation (`omni-moderation-latest`) on the instruction/all
  uploaded structured strings and on every string in the complete Extracter
  output; **G2** a blocking jailbreak/prompt-injection classifier on the same
  bounded input strings (runs before any container); **G3** lossless bounded
  prompt-injection screening over every Extracter-output string plus a compact
  academic/physics/on-topic/is-a-question classification over the extracted
  question records only. Cardinality, nesting, node, string, and byte limits
  reject pathological structured payloads instead of silently omitting fields.
  Each classifier phase is capped by
  `OPENAI_GUARDRAIL_STAGE_TIMEOUT_SECONDS` and
  `OPENAI_GUARDRAIL_MAX_CLASSIFIER_REQUESTS` in addition to the per-request
  `OPENAI_GUARDRAIL_TIMEOUT_SECONDS`; budget exhaustion always blocks before
  another call. Raw document bytes are read by the sandboxed extraction-only
  scope before G3; it has no application secrets and a strict domain
  allowlist, and its entire mutable state is retired before downstream tools.
  Config comes from `agents.json` `_config.guardrails` (via the
  `registry.guardrail_*` accessors); classifier prompts
  `data/prompts/{input,content}_guardrail.txt`; `GUARDRAILS_ENABLED` toggles the
  semantic/model checks, while structural size/complexity bounds remain active.
- **[agents.py](src/workflow/agents.py)** — `create_agents(...)` builds an exact requested Agents SDK stage subset from `agents.json` (model, reasoning effort, Code-Interpreter tool, latexifier compile tool, prompt instructions). `validate_workflow_assets()` locally checks every registry-owned prompt, the active generator variant, container templates/placeholders, and the LaTeX template before the input guardrail makes the first paid request. Code-Interpreter stages require an explicit owned `container_id` by default; production paths never use SDK auto-containers. The engine builds only `extracter` in the source scope and only `verifier`/`generator`/`latexifier` downstream. `generation_strategy()` reads `GENERATION_STRATEGY` (`holistic` default | `decompose`, which loads `generator_decompose.txt`). `container_expires_minutes()`, `any_agent_uses_code_interpreter()`.
- **[payloads.py](src/workflow/payloads.py)** — canonical input builders: `build_normalizer_input`, `build_extracter_input` (+ `build_extracter_retry_input`), `build_verifier_input` (+ `build_verifier_repair_input`), `build_generator_input` (+ `build_generator_repair_input`), `build_style_review_input`, `build_latex_input`, `build_evaluation_final_result`.
- **[resume.py](src/workflow/resume.py)** — partial-run helpers: `should_run_agent()`, `agent_reasoning_level()`, `extract_agent_input_payload()`/`runner_input_from_upload()`, `load_resume_agent_output()`, and the lenient `coerce_{normalizer,extracter,verifier,generator,latex}_from_agent_input()` builders that rebuild a stage's context from varied uploaded shapes.
- **[store.py](src/workflow/store.py)** — `WorkflowStore`: a small `(kind, id) → JSON` SQLite store (WAL, 30s busy timeout) used to mirror in-memory workflow/batch state for restart recovery. A separate atomic `idempotency_keys` table binds paid-start keys to endpoint/request fingerprints, original resource IDs, and start responses. Exclusive single-process startup commits interrupted claims whose resource exists and releases claims interrupted before resource persistence, so a crash cannot strand a key in `pending`. The database and WAL/SHM sidecars are regular-file checked and forced to owner-only mode `0600`. `save`/`load`/`delete`/`list_records`.
- **[stage_cache.py](src/workflow/stage_cache.py)** — `StageCache`: optional per-stage output reuse, **off unless `ENABLE_STAGE_CACHE` is truthy**. `make_key()` hashes the stage input + model + reasoning + a prompt-instructions hash (+ source-file content hashes for the extracter); `lookup()`/`store()` back onto `WorkflowStore` (kind `stage_cache`). The cache is bounded by `STAGE_CACHE_TTL_SECONDS=86400`, `STAGE_CACHE_MAX_ENTRIES=128`, and `STAGE_CACHE_MAX_RECORD_BYTES=2097152` defaults. Exact-match now; a clean seam for a semantic backend later.
- **[__init__.py](src/workflow/__init__.py)** — re-exports `create_agents`, `run_workflow_async`, the engine helpers, and the resume helpers for convenient imports.

### API layer — `src/api/`

- **[state.py](src/api/state.py)** — the shared brain for all routers. Holds the in-memory registries (`workflows`, `workflow_tasks`, `batch_jobs`, `batch_tasks`, `eval_batch_jobs`, `eval_batch_tasks`) and the `WorkflowStore` mirror; defines upload/retention/admission/concurrency config; repairs mutable upload/output/runtime roots to owner-only `0700`; the lazy `get_openai_client()`; the **workflow-runner wrappers** (`run_workflow_async`, `run_workflow_from_agent_async`, `run_workflow_from_agent_input_async`, `run_create_data_batch_async`, `run_eval_batch_async`) that adapt the engine to FastAPI state; a compact status DTO that omits full agent payloads; startup reconciliation; graceful shutdown; and the background `pruning_loop()` (TTL expiry, "lost"-run marking, max-entry trimming, artifact cleanup — off the request path). `OPENAI_WORKFLOW_CONCURRENCY=1` bounds full executions, `MAX_ACTIVE_WORKFLOWS=4` bounds admitted non-terminal records, and `OPENAI_WORKFLOW_BLOCKING_CONCURRENCY=4` bounds blocking worker calls. One process-local admission lock and reservation pool covers direct starts and every Create Data child; reservations survive awaited setup and are released on success, failure, or cancellation. The same owner also retains the Pi activity-lock lease until provider/artifact cleanup completes. Upload cleanup is serialized with live upload-disk reservations. Legacy per-workflow token queues exist only when `WORKFLOW_TOKEN_STREAMING_ENABLED` is truthy (default false). **In-memory dicts are the live source of truth; SQLite is the single-process recovery mirror, not resumable distributed orchestration.**
- **[upload_budget.py](src/api/upload_budget.py)** — thread-safe, process-wide upload storage admission. It inventories actual bytes under `UPLOADS_DIR`, substitutes reservations for in-progress session directories to avoid double counting, expands reservations before chunk writes, and returns HTTP 507 before concurrent or abandoned sessions can cross `UPLOAD_MAX_BYTES`. Its cleanup guard prevents the hourly pruner from deleting an in-progress reserved session.
- **[idempotency.py](src/api/idempotency.py)** — validates required
  `X-Idempotency-Key` headers, hashes canonical request inputs (including
  uploaded bytes), atomically selects one owner, returns the stored start
  response to identical concurrent/restarted submissions, and rejects
  different-input reuse with HTTP 409. Mappings contain no prompt/file body and
  are retained for at least seven days and for as long as their original
  workflow/batch state record remains retained.

The routers hold only route handlers (state lives in `state.py`):

| Router | Routes |
|---|---|
| [health.py](src/api/health.py) | `GET /api/health`, protected `GET /api/readiness` (booleans only), `GET /api/deployment/status`, `GET /api/openai/status`, `GET /version.json` |
| [config.py](src/api/config.py) | public `GET /api/public-config` (auth-required flag + validated optional donation URL), `GET /api/dropdown` (themes), `GET /api/rubric`, `GET /api/agents`, `GET /api/prompts/{prompt_name}`, `GET /api/evaluation/files` |
| [files.py](src/api/files.py) | `POST /api/upload` (5 files / 20 MB each, `MAX_UPLOAD_TOTAL_BYTES` total cap ≈60 MB; random capability-like sessions, explicit file IDs, streamed writes, original-extension checks, true-prefix sanity checks for accepted binary formats, live upload-disk reservations, failure cleanup), `GET /api/download/{workflow_id}/{file_path}`, `GET /api/download-zip/{record_id}` for workflows or batch jobs (terminal owner-task gate, single-flight atomic creation, artifact-fingerprint freshness checks, symlink rejection, `DOWNLOAD_ZIP_MAX_SOURCE_BYTES` default 128 MiB / hard maximum 512 MiB; the browser buffers the resulting ZIP blob), and contained `GET /api/output-artifact` for generated batch artifacts |
| [workflows.py](src/api/workflows.py) | `POST /api/workflow/start`, `POST /api/workflow/{id}/stop`, `POST /api/workflow/rerun-from-agent`, `POST /api/workflow/start-from-agent-input`, `GET /api/workflow/{id}/status` (compact payload carries `blocked` but not full agent input/output), `GET /api/workflow/{id}/result` (full terminal detail), `GET /api/workflow/{id}/stream` (token deltas only when explicitly enabled; polling stays authoritative). Evaluation starts require both opt-in and explicit maximum-grader-call confirmation. |
| [batch.py](src/api/batch.py) | `POST /api/create-data/start` (requires explicit full-workflow cost confirmation; no rubric evaluation), `POST /api/create-data/{id}/stop`, `GET /api/create-data/{id}/status`, `GET /api/create-data/{id}/result` |
| [eval_batch.py](src/api/eval_batch.py) | `GET /api/eval-batch/outputs`, `POST /api/eval-batch/start` (`prior_runs` or `upload` mode; returns and confirms grader plus isolated-PDF extraction/bootstrap projections), `GET /api/eval-batch/{id}/status`, `GET /api/eval-batch/{id}/result`, `POST /api/eval-batch/{id}/stop` (cooperative best-effort; an in-flight remote eval may finish) |
| [professor_comparison.py](src/api/professor_comparison.py) | `POST /api/outputs/{label}/compare-professor` (writes comparison artifacts under that batch's `XLSX/`) |

The five provider-cost-bearing start routes require a unique client-generated
`X-Idempotency-Key` (16-128 characters matching `[A-Za-z0-9._~:-]`).
Clients must reuse that key only for a manual retry of the exact same request.
The server never schedules a second resource for the same key/fingerprint and
returns HTTP 409 if the endpoint or inputs differ. A duplicate still being
accepted can return HTTP 425 with `Retry-After: 1`; keep the same key, but do
not turn that response into an automatic paid-POST retry loop.

When the access token is configured, the only public API/bootstrap GETs are
`/api/health`, `/api/openai/status`, `/api/public-config`, and `/version.json`.
The boundary is one shared token, not student accounts or role-based access.

### Evaluation & validation

- **[evaluation.py](src/evaluation.py)** — OpenAI Evals integration. `run_openai_output_evaluation()` builds the dataset, creates the eval, polls/cancels the run, aggregates per-criterion scores, and writes the report + spreadsheet + rubric snapshot. `_build_testing_criteria()` turns each of eight rubric criteria into an OpenAI Evals `score_model` spec, so `N` evaluated items produce approximately `8 × N` grader calls. Every grader is seeded with `GRADER_SEED = 7`, has a generated-token ceiling from `GRADER_MAX_COMPLETION_TOKENS` (default 4096), and uses criterion-specific reasoning effort; the item schema feeds source `distractor_logic` to the judge; `FAILURE_TAGS` prefix failing rationales; the model is overridable via `GRADER_MODEL_OVERRIDE`. Unknown source IDs, missing items, missing criterion coverage, failed/canceled/timeout states, and scored/received/submitted counts remain explicit. Temporary provider files have a fallback expiry and normal cleanup best-effort deletes the file/run/eval definition. `parse_sample_steps` is exported. Evaluation is opt-in for ordinary web and CLI runs. The current Evals API becomes read-only October 31, 2026 and shuts down November 30, 2026 per the [official OpenAI deprecations page](https://developers.openai.com/api/docs/deprecations); a replacement is a release requirement.
- **[eval_pipeline.py](src/eval_pipeline.py)** — helpers for batch evaluation *without* a full workflow: `extract_questions_from_pdf()` provisions one fresh document-bootstrapped container per untrusted PDF, applies the registry Extracter turn cap and one bounded retry, runs the per-PDF moderation/content classifiers, and cleans both file/container resources; `question_to_generated_item()` and `build_eval_final_result()` construct grader input. Upload-mode cost projection therefore includes `F` bootstraps and up to `2 × F` Extracter calls for `F` isolated PDFs, plus variable classifier batching, not only the grader fan-out.
- **[pipeline_validation.py](src/pipeline_validation.py)** — deterministic stage-handoff contracts. `validate_verifier_output()` and `validate_generator_output()` return a `ValidationReport` (`valid`, `errors`, `repair_source_ids`). `validate_distractor_logic_preservation()` enforces that every source misconception label survives into the generated options using a wording-invariant token signature (`_logic_signature()`) and Jaccard containment (`_logic_preserved()`).

### Artifacts & container

- **[artifacts.py](src/artifacts.py)** — per-run artifact I/O. `write_agent_artifacts()` appends each attempt's input/output to `agent_outputs/<agent>/agent_input.jsonl` + `agent_output.jsonl` and records metadata; `write_workflow_metadata()`/`load_workflow_metadata()` manage `agent_metadata_output.json`; `to_jsonable()`, JSONL helpers (`append_jsonl`/`read_jsonl`/`read_last_jsonl`), and payload extractors (`load_agent_input_payload`/`load_agent_output_payload`). The engine also archives source files under `input_files/` with collision-safe names and their real suffixes, so a run is portable beyond temporary upload paths. Re-exports `WORKFLOW_AGENT_SEQUENCE`/`AGENT_DISPLAY_NAMES` from the registry.
- **[container.py](src/container.py)** — `SharedWorkflowRuntime` wraps one active,
  explicitly owned provider container. The engine uses separate sequential
  instances for untrusted extraction and downstream tools: `bootstrap()`
  installs document dependencies only on an Extracter cache miss;
  `compile_latex()` lazily provisions TeX in the clean downstream scope and
  streams the PDF/TeX downloads through an atomic owner-only temporary file,
  rejecting either above `MAX_GENERATED_ARTIFACT_BYTES` (50 MiB default);
  `build_compile_latex_tool()` exposes that compile seam. Shell Responses calls
  use `setup.maxOutputTokens` (4096). `allowed_domains_from_env()` and
  `container_network_policy()` validate and enforce the outbound allowlist on
  every scope and replacement. OpenAI also requires an organization
  administrator to allow those domains; the request policy can only narrow the
  organization list, not expand it. The current six domains are
  `archive.ubuntu.com`, `security.ubuntu.com`, `deb.debian.org`,
  `security.debian.org`, `pypi.org`, and `files.pythonhosted.org`; keep both
  layers aligned and least-privilege per the
  [official shell-network guide](https://developers.openai.com/api/docs/guides/tools-shell#network-access).

### Tools — `src/tools/` (developer/analysis, not on the request path)

- **[judge_agreement.py](src/tools/judge_agreement.py)** — judge ↔ professor
  agreement primitives: `load_xlsx_grids()`, `compare_grids()`,
  `cohen_kappa()`, `write_disagreements_csv()`/`write_report_json()`. They are
  consumed by `tools/professor_comparison.py`.
- **[meta_evaluator.py](src/tools/meta_evaluator.py)** — a **PROPOSE-ONLY** grader self-improvement CLI (also `python -m backend.src.main improve`). Mines grader rationales + `FAILURE_TAGS` + deterministic checks and emits ranked, file-targeted proposals; has a reward-hacking guard, an optional labeled κ-gate (`--labels`/`--kappa-floor`) with a default minimum of five non-N/A comparisons per criterion (`--min-kappa-samples`), an experiment ledger (`runtime/meta_eval_ledger.jsonl`), and a versioned calibration format (`data/calibration/*.jsonl`). Reports record prompt/rubric SHA-256 content hashes, including dirty content. Self-tag-only proposals may still be emitted at lower rank and are marked non-independent; treat them as hypotheses until deterministic or professor evidence corroborates them. **Never auto-applies** changes; wrapped by the `.agents/skills/grader-review/SKILL.md` skill.
- **[style_testing.py](src/tools/style_testing.py)** + **[check_generator_style.py](src/tools/check_generator_style.py)** — detect generator verbosity/style drift vs source; `python -m backend.src.tools.check_generator_style <output-folder>`.
- **[validate_test_fixtures.py](src/tools/validate_test_fixtures.py)** — validate the saved `data/testing/` fixtures against the schemas; `python -m backend.src.tools.validate_test_fixtures`.

---

## Data files — `data/` (hand-curated; edit only when asked)

- **[agents.json](data/agents.json)** — the single source of truth for all 10 nodes. Common metadata includes `sequence`, `name`, `model`, `reasoning`, `kind`, `isLLM`, `hasPrompt`, `acceptsSourceFiles`, `isStoppable`, `useCodeInterpreter`, `countLabel`, `aliases`, `inputFrom`/`outputTo`, and `cliDescription`; counted nodes add `countField`, result-bearing nodes add `resultField`, normal Agents SDK stages add `maxOutputTokens`, and agents may add `maxTurns`. `evaluation` adds `promptName`; `setup` adds `allowedDomains`, `containerPythonPackages`, and its shell-task `maxOutputTokens`; `_config` holds `themes`, `apiTitle`, `apiDescription`, and `guardrails` including classifier `maxOutputTokens`. The two `kind: "guardrail"` nodes (`input_guardrail`, `content_guardrail`) carry sequences `0.5`/`2.5`. Read through `registry.py`. Editing it requires a backend restart (lru-cache).
- **[prompts/](data/prompts/)** — one `*.txt` per agent (`normalizer`, `extracter`, `verifier`, `generator`, `generator_decompose`, `latexifier`, `evaluator`), the two guardrail classifier prompts (`input_guardrail.txt`, `content_guardrail.txt`), plus the container shell-task templates `bootstrap.txt` / `bootstrapCompile.txt` (with `{var}` placeholders). **Versioned separately** — after any prompt edit run, from `site/`, `.venv/bin/python scripts/prompt_versions.py snapshot -m "…"`.
- **[rubric.json](data/rubric.json)** — the 8 evaluation criteria (pass/fail), each with `question`, `guidance[]`, `scoring_guide[]`, and an optional per-criterion `reasoning` effort. The grader model comes from `agents.json` `evaluation.model` (overridable via `GRADER_MODEL_OVERRIDE`). There is **no** LaTeX-quality criterion (the old `latex_syntax_quality` was removed and must not be reintroduced).
- **[calibration/](data/calibration/)** — human-maintained, versioned
  calibration datasets (`*.jsonl`) consumed by `tools/meta_evaluator.py`.
- **[latex_template.tex](data/latex_template.tex)** — the LaTeX preamble/macros injected into the latexifier prompt at render time.
- **[testing/](data/testing/)** — saved agent-input fixtures (`agent_inputs/<agent>/*.json`) + source/prompt samples used by `validate_test_fixtures.py` and the per-agent CLI.

---

## Tests — `tests/`

Run from `site/`:
`OPENAI_API_KEY= APP_ACCESS_TOKEN= .venv/bin/python -m pytest`. Blank both
values so ignored local environment files cannot turn an offline suite into a
live-provider or authenticated-boundary run. Do not hardcode an expected test
count in documentation; report the count produced by the current suite.
Representative coverage by file (not exhaustive):

| Test | Covers |
|---|---|
| `test_agent_instructions.py` | Prompt loading + per-agent instruction assembly |
| `test_agent_isolation.py` | Each agent produces its expected output shape in isolation |
| `test_api_routes.py` | HTTP route handlers (upload/workflow/batch/eval) |
| `test_cli.py` | The `main.py` argparse subcommand surface + backward-compatible dispatch |
| `test_distractor_logic_preservation.py` | `validate_distractor_logic_preservation` signature/Jaccard logic |
| `test_engine_hardening.py` | `run_llm_stage` streaming/`max_turns`/typed-error handling |
| `test_engine_workflow.py` | Full-run, input-to-agent, and resume-from-cache engine paths |
| `test_evaluation_aggregation.py` | Per-criterion aggregation, `na_count`, coverage mismatch |
| `test_evaluation_contract.py` | Eval dataset/criteria construction contract |
| `test_grader_hardening.py` | Grader seed, per-criterion reasoning, failure tags, model override |
| `test_guardrails.py` | The three guardrails + fail-closed `blocked` behavior |
| `test_judge_agreement.py` | Cohen's κ + grid comparison |
| `test_meta_evaluator.py` | Propose-only meta-evaluator, reward-hacking guard, κ-gate, ledger |
| `test_pipeline_validation.py` | Verifier/generator output contracts |
| `test_prompt_versions.py` | The prompt-snapshot/restore/show CLI |
| `test_registry_guardrails.py` | Guardrail accessors + runnable/stoppable/LLM set exclusion |
| `test_safety_store.py` | `safety.py` helpers + `WorkflowStore` |
| `test_server_state.py` | API state helpers, compact status payloads, startup reconciliation, graceful shutdown, pruning |
| `test_style_testing.py` | Generator style-drift detection |
| `test_upload_hardening.py` | Original-extension rejection, common binary prefix checks, total-size cap |
| `test_upload_disk_budget.py` | Actual-inventory/concurrent upload admission, reservation growth, cleanup coordination, HTTP 507 boundary |
| `test_workflow_e2e.py` | End-to-end pipeline + stop-after/no-eval |
| `test_workflow_payloads.py` | `build_*_input` payload shapes |

---

## Conventions & gotchas

- **Everything resolves relative to `site/`** (`REPO_ROOT`); run backend commands from there (or `uvicorn … --app-dir site`).
- **No module-load side effects** in new files — use lazy accessors (`@lru_cache`) or first-call loaders. `registry._load_raw` and the `evaluation.py` rubric/grader accessors are cached (clear in tests that swap config).
- **Engine never imports HTTP/CLI state** — it takes callbacks; don't import FastAPI types into `workflow/`.
- **All agent constants come from `registry.py`** (never hardcode `"normalizer"`, etc.); paths come from `paths.py`.
- **Structured outputs** are enforced via Pydantic `output_type=` on each agent (optional/nullable fields like `classification`/`decomposition` stay backward-compatible).
- **Reasoning effort and models** are per-agent in `agents.json`. The two-tier split is
  intentional: `gpt-5.6-terra` (high reasoning) for the Generator, `gpt-5.6-luna` for
  every other LLM stage and the grader.
- **Active in-flight runs do not resume across a server restart** (in-memory + SQLite mirror).
