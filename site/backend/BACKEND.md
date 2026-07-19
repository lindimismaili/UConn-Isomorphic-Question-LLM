# Backend Reference — `site/backend/`

The FastAPI + OpenAI Agents SDK backend that powers the Isomorphic Question
Generator. It exposes an HTTP API (and a CLI) that drives a 10-node agent
pipeline (6 LLM agents + 2 runtime + 2 guardrail): read uploaded physics
questions, verify them, generate isomorphic variants, render a PDF, and grade
the result — with two safety guardrails inline.

This file documents **what every backend file does, the important functions,
and how the workflow is structured**. Repo-wide conventions and the data
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
│   ├── paths.py  bootstrap.py  safety.py      # foundation
│   ├── registry.py  schemas.py  helpers.py    # config gateway, types, utils
│   ├── artifacts.py  container.py             # output I/O + OpenAI container
│   ├── evaluation.py  eval_pipeline.py  pipeline_validation.py
│   ├── api/            # FastAPI routers + shared state
│   ├── workflow/       # the engine, agent factory, payloads, resume, stores, guardrails
│   └── tools/          # developer/analysis CLIs (not on the request path)
├── outputs/            # per-run artifacts, grouped <mm-dd-yyyy>/<hh-mm-ss> (gitignored contents)
├── uploads/            # per-session uploads, grouped <mm-dd-yyyy>/<hh-mm-ss> (gitignored contents)
└── runtime/            # SQLite stores (gitignored contents)
```

---

## The 10-node pipeline

One workflow runs the nodes **in order**; each consumes the previous output.
`setup` and `output` are `kind: "runtime"` (no LLM); `input_guardrail` and
`content_guardrail` are `kind: "guardrail"`; the middle six are LLM agents. The
engine ([workflow/engine.py](src/workflow/engine.py)) drives them via
`run_workflow_async()`.

| # | Node | Type | What it does |
|---|---|---|---|
| 0 | `setup` | runtime | Create one shared OpenAI Code-Interpreter container, upload source files, bootstrap LaTeX + Python deps (kicked off in the background, awaited at latexifier). The G2 jailbreak guardrail runs **before** the container is created. |
| 0.5 | `input_guardrail` | guardrail | OpenAI Moderation + a blocking jailbreak/prompt-injection classifier on the raw instruction. |
| 1 | `normalizer` | agent | Parse the user prompt into a structured config (count, difficulty, theme, title, notes, show-answers). |
| 2 | `extracter` | agent | Read uploaded files and extract source questions (stem, options, type, figure-dependence, grouping). Retries once if zero questions. |
| 2.5 | `content_guardrail` | guardrail | Moderation + an academic/physics on-topic + is-a-question classifier on the extracter output. |
| 3 | `verifier` | agent | Solve/validate answers, fill per-option explanations + `distractor_logic`, and emit a per-question `classification`. |
| 4 | `generator` | agent | Produce isomorphic questions (holistic or decompose strategy), preserving concept/structure/distractor roles. |
| 5 | `latexifier` | agent | Render the questions as LaTeX and compile a PDF inside the container. |
| 6 | `evaluation` | agent | Run the OpenAI Evals rubric (8 criteria) over source↔generated pairs; write the report + spreadsheet. |
| 7 | `output` | runtime | Write `final_result.json`, `agent_outputs/` artifacts, and the downloadable file manifest. |

Guardrail nodes are display/animation-only pipeline nodes; they are **excluded
from the runnable/stoppable/LLM sets** (`registry.input_to_agent_sequence()`,
`llm_agents()`, and `stoppable_agents()` skip `kind == "guardrail"`). When a
guardrail fails closed the run ends in the terminal **`blocked`** status (distinct
from `error`): the engine sets `workflow["blocked"] = {category, reason, stage}`.
See [workflow/guardrails.py](src/workflow/guardrails.py).

**Stage execution & hardening.** Every stage runs through the engine's
`run_llm_stage()` helper: it streams via `Runner.run_streamed` when an
`emit_token` callback is passed (server), else `await Runner.run` (CLI/tests), and
catches `MaxTurnsExceeded` / `ModelBehaviorError` / `ModelRefusalError` → a clean
per-stage `ValueError`. Each stage gets an explicit `max_turns` (optional
`agents.json` `maxTurns`; default 6, latexifier 12). A container heartbeat +
latexifier recreate-on-expiry keep the shared container alive.

**Resume / partial runs.** Any stage can be the starting point: the engine skips
upstream stages and rebuilds their context either from a previously cached run
(`rerun-from-agent`) or from an uploaded payload (`start-from-agent-input`). It
can also stop early (`stop_after_agent`) and skip evaluation (`run_evaluation=False`).

**Engine shared helpers** (reused by every stage instead of copy-pasting):
- `finalize_agent_run(...)` — mark a freshly-run stage completed, mirror its output into the live workflow dict (for the UI), and persist input/output/metadata artifacts.
- `resolve_upstream_agent(...)` — reconstruct a skipped stage from upload (`from_input`) or cache (`from_cache`), applying the same post-processing and recording "provided"/"reused" state.
- `copy_or_record_reused_agent_artifact(...)`, `mark_provided_agent_state(...)`, `mark_reused_agent_state(...)`, `record_provided_agent_artifact(...)` — state/artifact bookkeeping for the resume paths.
- `materialize_latex_output(...)` — write the `.tex` safely, compile to PDF in the container, return canonical paths.
- `file_metadata(...)` — API-facing file descriptor (id, size, mediaType, downloadable).

The engine takes callbacks (`touch_workflow`, `to_thread`, `load_rubric_model`,
`load_rubric_payload`) so it never imports FastAPI/CLI state — the same engine
serves the server and the CLI.

---

## Source modules

### Entry points

| File | Run with | Purpose |
|---|---|---|
| [src/server.py](src/server.py) | `uvicorn backend.src.server:app` | FastAPI app factory. `lifespan()` starts the background `pruning_loop()`; registers the domain routers (health, config, files, workflows, batch, eval_batch), CORS, and a static mount that serves `site/dist/` (falls back to an HTML notice if `index.html` is absent). `app` is built at import. |
| [src/main.py](src/main.py) | `python -m backend.src.main <cmd> [opts]` | CLI workflow orchestrator with **full web-app parity** over the shared engine. Argparse subcommands: `run`, `rerun-from-agent`, `from-agent-input`, `batch`, `eval`, `improve`, `show`, `zip`, `list` (`agents\|themes\|prompts\|rubric\|eval-files\|runs`), `prompt` (reuses the server batch/eval coroutines). Backward-compatible: leading files/flags dispatch to `run` (`… "<pdf>" -n 2 -i "…"` still works). OpenAI client is created lazily so `--help` needs no key. |
| [src/cli_agents.py](src/cli_agents.py) | `python -m backend.src.cli_agents <cmd>` | Per-agent CLI for isolated testing: `list`, `schema <agent>`, `validate <agent> --input`, `run <agent> --input`, `run-one <agent> --input`. |

### Foundation

- **[src/paths.py](src/paths.py)** — canonical filesystem paths computed once at import (no I/O). `REPO_ROOT` = parent of `backend/` (i.e. `site/`); exports `ENV_PATH`, `DATA_DIR`, `PROMPTS_DIR`, `AGENTS_CONFIG_PATH`, `RUBRIC_PATH`, `LATEX_TEMPLATE_PATH`, `OUTPUTS_DIR`, `UPLOADS_DIR`, `RUNTIME_DIR`, `DIST_DIR`, `VERSION_PATHS`. **Import paths from here — never recompute `__file__` chains.**
- **[src/bootstrap.py](src/bootstrap.py)** — `load_dotenv(path)` populates `os.environ` from `.env` with `setdefault` (real env wins). Called explicitly by the server and CLIs.
- **[src/safety.py](src/safety.py)** — filesystem/artifact safety: `safe_filename()`/`safe_stem()` (sanitize), `ensure_child_path()` (path-traversal guard), `create_unique_run_dir()`/`unique_child_name()` (collision-free output dirs), `stable_short_hash()` (deterministic short hashes for ids/cache keys), `write_json()`, `utc_run_timestamp()`/`utc_now_iso()`.
- **[src/registry.py](src/registry.py)** — **lazy gateway to `agents.json`.** `_load_raw()` is `@lru_cache`'d. Accessors: `agent_sequence()`, `all_agents()`, `agent_entry(key)`, filtered views `input_to_agent_sequence()`/`llm_agents()`/`stoppable_agents()`/`source_file_agents()`/`promptable_agents()` (the first three skip `kind == "guardrail"`), naming `display_names()`/`cli_descriptions()`/`count_labels()`/`aliases_map()`, setup/container `setup_bootstrap_model()`/`setup_allowed_domains()`/`setup_container_packages()`, guardrail config `guardrails_enabled()`/`guardrail_model()`/`guardrail_moderation_model()`/`guardrail_reasoning()`/`guardrail_physics_min_confidence()`/`guardrail_block_off_topic()`/`guardrail_fail_closed()`/`guardrail_max_questions_sampled()`/`guardrail_input_node()`/`guardrail_content_node()`/`guardrail_nodes()`, plus `evaluation_model()`, `themes()`, `api_title()`, `api_description()`, `default_from_agent()`. **Don't hardcode agent keys — import from here.** In tests that swap `AGENTS_CONFIG_PATH`, call `registry._load_raw.cache_clear()`.
- **[src/schemas.py](src/schemas.py)** — all Pydantic models. Question structure: `OptionItem`, `QuestionPart`, `SourceQuestionItem`, `NewQuestionItem`, `QuestionClassification` (verifier-derived profile), `QuestionDecomposition` (decompose-strategy audit). Per-stage outputs: `NormalizerOutput`, `ExtracterOutput`, `VerifierOutput`, `GeneratorOutput`, `LaTeXOutput`. End-to-end `WorkflowResult`. `AGENT_OUTPUT_TYPES`/`AGENT_INPUT_TYPES` map agent keys → models (used as the Agents SDK `output_type`).
- **[src/helpers.py](src/helpers.py)** — shared utils: `load_agents_config()`, prompt loading `resolve_prompt_path()`/`load_prompt()`/`render_prompt()` (`{var}` substitution via plain replace), `upload_file_to_openai()`, `build_json_message()` (wrap JSON + file refs as an OpenAI user message), `apply_requested_difficulty()`, `merge_extracted_metadata_into_config()`, `normalize_question_types()`.

### Workflow engine — `src/workflow/`

- **[engine.py](src/workflow/engine.py)** — `run_workflow_async()` orchestrates the 10 nodes and the shared helpers described above. Container creation + uploads happen before any agent; each agent call runs inside a `trace()` context and through `run_llm_stage()` (streaming/`max_turns`/typed-error handling). It runs the guardrails at their points in the sequence and, on a fail-closed trip, ends the run in the terminal `blocked` status. Repair/style sub-runs get a `custom_span`; eval filesystem I/O is non-blocking (`to_thread`).
- **[guardrails.py](src/workflow/guardrails.py)** — the three safety checks: **G1** OpenAI Moderation (`omni-moderation-latest`) on the instruction + extracted text; **G2** a blocking jailbreak/prompt-injection classifier on the raw instruction (runs before the container is created); **G3** an academic/physics on-topic + is-a-question classifier on the extracter output. Fail-closed → terminal `blocked` status. Config from `agents.json` `_config.guardrails` (via the `registry.guardrail_*` accessors); classifier prompts `data/prompts/{input,content}_guardrail.txt`; toggle with env `GUARDRAILS_ENABLED`.
- **[agents.py](src/workflow/agents.py)** — `create_agents(...)` builds the Agents SDK agent set from `agents.json` (model, reasoning effort, Code-Interpreter tool, latexifier's compile tool, instructions from prompts). `generation_strategy()` reads `GENERATION_STRATEGY` (`holistic` default | `decompose`, which loads `generator_decompose.txt`). `container_expires_minutes()`, `any_agent_uses_code_interpreter()`.
- **[payloads.py](src/workflow/payloads.py)** — canonical input builders: `build_normalizer_input`, `build_extracter_input` (+ `build_extracter_retry_input`), `build_verifier_input` (+ `build_verifier_repair_input`), `build_generator_input` (+ `build_generator_repair_input`), `build_style_review_input`, `build_latex_input`, `build_evaluation_final_result`.
- **[resume.py](src/workflow/resume.py)** — partial-run helpers: `should_run_agent()`, `agent_reasoning_level()`, `extract_agent_input_payload()`/`runner_input_from_upload()`, `load_resume_agent_output()`, and the lenient `coerce_{normalizer,extracter,verifier,generator,latex}_from_agent_input()` builders that rebuild a stage's context from varied uploaded shapes.
- **[store.py](src/workflow/store.py)** — `WorkflowStore`: a small `(kind, id) → JSON` SQLite store (WAL, 30s busy timeout) used to mirror in-memory workflow/batch state for restart recovery. `save`/`load`/`delete`/`list_records`.
- **[stage_cache.py](src/workflow/stage_cache.py)** — `StageCache`: optional per-stage output reuse, **off unless `ENABLE_STAGE_CACHE` is truthy**. `make_key()` hashes the stage input + model + reasoning + a prompt-instructions hash (+ source-file content hashes for the extracter); `lookup()`/`store()` back onto `WorkflowStore` (kind `stage_cache`). Exact-match now; a clean seam for a semantic backend later.
- **[__init__.py](src/workflow/__init__.py)** — re-exports `create_agents`, `run_workflow_async`, the engine helpers, and the resume helpers for convenient imports.

### API layer — `src/api/`

- **[state.py](src/api/state.py)** — the shared brain for all routers. Holds the in-memory registries (`workflows`, `workflow_tasks`, `batch_jobs`, `batch_tasks`, `eval_batch_jobs`, `eval_batch_tasks`) and the `WorkflowStore` mirror; defines the env-var config (upload limits, TTL/max-entries, prune cadence, concurrency); the lazy `get_openai_client()`; the **workflow-runner wrappers** (`run_workflow_async`, `run_workflow_from_agent_async`, `run_workflow_from_agent_input_async`, `run_create_data_batch_async`, `run_eval_batch_async`) that adapt the engine to FastAPI state; and the background `pruning_loop()` (TTL expiry, "lost"-run marking, max-entry trimming, artifact cleanup — off the request path). It also owns the **per-workflow token queue** that the engine's `emit_token` callback feeds and the SSE `/stream` endpoint drains. A `Semaphore(OPENAI_WORKFLOW_BLOCKING_CONCURRENCY)` bounds concurrent runs. **In-memory dicts are the source of truth; SQLite is the recovery mirror.**

The routers hold only route handlers (state lives in `state.py`):

| Router | Routes |
|---|---|
| [health.py](src/api/health.py) | `GET /api/health`, `GET /api/openai/status`, `GET /version.json` |
| [config.py](src/api/config.py) | `GET /api/dropdown` (themes), `GET /api/rubric`, `GET /api/agents`, `GET /api/prompts/{prompt_name}`, `GET /api/evaluation/files` |
| [files.py](src/api/files.py) | `POST /api/upload` (5 files / 20 MB each, `MAX_UPLOAD_TOTAL_BYTES` total cap ≈60 MB; rejects disallowed types on the **original** extension + PDF magic-byte check via `UPLOAD_MAGIC_SIGNATURES`), `GET /api/download/{workflow_id}/{file_path}`, `GET /api/download-zip/{workflow_id}` (cached zip) |
| [workflows.py](src/api/workflows.py) | `POST /api/workflow/start`, `POST /api/workflow/rerun-from-agent`, `POST /api/workflow/start-from-agent-input`, `GET /api/workflow/{id}/status` (payload carries a `blocked` field), `GET /api/workflow/{id}/result`, `GET /api/workflow/{id}/stream` (real SSE: named events `token` `{agent,delta}`, `status`, `done`; polling stays authoritative) |
| [batch.py](src/api/batch.py) | `POST /api/create-data/start`, `POST /api/create-data/{id}/stop`, `GET /api/create-data/{id}/status`, `GET /api/create-data/{id}/result` |
| [eval_batch.py](src/api/eval_batch.py) | `GET /api/eval-batch/outputs`, `POST /api/eval-batch/start` (`prior_runs` or `upload` mode), `GET /api/eval-batch/{id}/status`, `GET /api/eval-batch/{id}/result`, `POST /api/eval-batch/{id}/stop` |

### Evaluation & validation

- **[evaluation.py](src/evaluation.py)** — OpenAI Evals integration. `run_openai_output_evaluation()` builds the dataset, creates the eval, polls the run, aggregates per-criterion scores, and writes the report + spreadsheet + rubric snapshot. The grader system prompt (`general_grader_instructions()`, from `evaluator.txt`, with anti-bias + instruction-hierarchy framing) and rubric accessors are `@lru_cache`'d. `_build_testing_criteria()` turns each rubric criterion into an OpenAI Evals `score_model` spec. Notable details: every grader is seeded with `GRADER_SEED = 7` (surfaced as report `grader_seed`); reasoning effort is **per-criterion** (rubric.json per-criterion `reasoning`; `physics_correctness` + `distractor_structure_preservation` = high, `preserve_physical_content` + `solvable_and_specified` = medium, rest inherit `low`); the item schema feeds source `distractor_logic` to the judge via a `source_distractor_logic` field; a `FAILURE_TAGS` taxonomy has the grader prefix a `[TAG]` on fails; the model is overridable via env `GRADER_MODEL_OVERRIDE`. The report adds `reasoning_effort_by_criterion` and per-criterion `failure_tag_counts`; `_aggregate_question_scores()` records `na_count`/`na_rate` and a `coverage_mismatch` when fewer items return than were submitted. `parse_sample_steps` is exported for reuse. **There is no deterministic LaTeX-log grading criterion** — LaTeX quality is not one of the 8 rubric criteria.
- **[eval_pipeline.py](src/eval_pipeline.py)** — helpers for batch evaluation *without* a full workflow: `extract_questions_from_pdf()` (upload + extract), `question_to_generated_item()`, `build_eval_final_result()`.
- **[pipeline_validation.py](src/pipeline_validation.py)** — deterministic stage-handoff contracts. `validate_verifier_output()` and `validate_generator_output()` return a `ValidationReport` (`valid`, `errors`, `repair_source_ids`). `validate_distractor_logic_preservation()` enforces that every source misconception label survives into the generated options using a wording-invariant token signature (`_logic_signature()`) and Jaccard containment (`_logic_preserved()`).

### Artifacts & container

- **[artifacts.py](src/artifacts.py)** — per-run artifact I/O. `write_agent_artifacts()` appends each attempt's input/output to `agent_outputs/<agent>/agent_input.jsonl` + `agent_output.jsonl` and records metadata; `write_workflow_metadata()`/`load_workflow_metadata()` manage `agent_metadata_output.json`; `to_jsonable()`, JSONL helpers (`append_jsonl`/`read_jsonl`/`read_last_jsonl`), and payload extractors (`load_agent_input_payload`/`load_agent_output_payload`). Re-exports `WORKFLOW_AGENT_SEQUENCE`/`AGENT_DISPLAY_NAMES` from the registry.
- **[container.py](src/container.py)** — `SharedWorkflowRuntime` wraps the one shared OpenAI container per workflow: `bootstrap()` (idempotent dependency install), `compile_latex()` (compile + download the PDF, returns `LatexCompileResult`), `build_compile_latex_tool()` (exposes compile as an agent function tool). `allowed_domains_from_env()` reads the outbound allowlist (env override of `setup.allowedDomains`).

### Tools — `src/tools/` (developer/analysis, not on the request path)

- **[judge_agreement.py](src/tools/judge_agreement.py)** — judge ↔ professor agreement: `load_xlsx_grids()`, `compare_grids()`, `cohen_kappa()`, `write_disagreements_csv()`/`write_report_json()` (backs `Other/compare_to_professor.py`).
- **[meta_evaluator.py](src/tools/meta_evaluator.py)** — a **PROPOSE-ONLY** grader self-improvement CLI (also `python -m backend.src.main improve`). Mines grader rationales + `FAILURE_TAGS` + deterministic checks and emits ranked, file-targeted proposals; has a reward-hacking guard, an optional labeled κ-gate (`--labels`/`--kappa-floor`), an experiment ledger (`runtime/meta_eval_ledger.jsonl`), and a versioned calibration format (`data/calibration/*.jsonl`). **Never auto-applies** changes; wrapped by the `.agents/grader-review/SKILL.md` skill.
- **[style_testing.py](src/tools/style_testing.py)** + **[check_generator_style.py](src/tools/check_generator_style.py)** — detect generator verbosity/style drift vs source; `python -m backend.src.tools.check_generator_style <output-folder>`.
- **[validate_test_fixtures.py](src/tools/validate_test_fixtures.py)** — validate the saved `data/testing/` fixtures against the schemas; `python -m backend.src.tools.validate_test_fixtures`.

---

## Data files — `data/` (hand-curated; edit only when asked)

- **[agents.json](data/agents.json)** — the single source of truth for all 10 nodes (each carries `sequence`, `name`, `model`, `reasoning`, `kind`, `isLLM`, `hasPrompt`, `acceptsSourceFiles`, `isStoppable`, `useCodeInterpreter`, `countLabel`/`countField`, `aliases`, `inputFrom`/`outputTo`, `cliDescription`, `resultField`, plus an optional `maxTurns`; `evaluation` adds `promptName`; `setup` adds `allowedDomains` + `containerPythonPackages`) plus `_config` (`themes`, `apiTitle`, `apiDescription`, `guardrails`). The two `kind: "guardrail"` nodes (`input_guardrail`, `content_guardrail`) carry sequences `0.5`/`2.5`. Read through `registry.py`. Editing it requires a backend restart (lru-cache).
- **[prompts/](data/prompts/)** — one `*.txt` per agent (`normalizer`, `extracter`, `verifier`, `generator`, `generator_decompose`, `latexifier`, `evaluator`), the two guardrail classifier prompts (`input_guardrail.txt`, `content_guardrail.txt`), plus the container shell-task templates `bootstrap.txt` / `bootstrapCompile.txt` (with `{var}` placeholders). **Versioned separately** — after any prompt edit run, from `site/`, `python scripts/prompt_versions.py snapshot -m "…"`.
- **[rubric.json](data/rubric.json)** — the 8 evaluation criteria (pass/fail), each with `question`, `guidance[]`, `scoring_guide[]`, and an optional per-criterion `reasoning` effort. The grader model comes from `agents.json` `evaluation.model` (overridable via `GRADER_MODEL_OVERRIDE`). There is **no** LaTeX-quality criterion (the old `latex_syntax_quality` was removed and must not be reintroduced).
- **[calibration/](data/calibration/)** — versioned meta-evaluator calibration snapshots (`*.jsonl`) written by `tools/meta_evaluator.py`.
- **[latex_template.tex](data/latex_template.tex)** — the LaTeX preamble/macros injected into the latexifier prompt at render time.
- **[testing/](data/testing/)** — saved agent-input fixtures (`agent_inputs/<agent>/*.json`) + source/prompt samples used by `validate_test_fixtures.py` and the per-agent CLI.

---

## Tests — `tests/`

Run from `site/`: `python -m pytest` (currently ~262 tests). Representative
coverage by file (not exhaustive):

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
| `test_server_state.py` | API state helpers, status payloads (incl. `blocked`), pruning |
| `test_style_testing.py` | Generator style-drift detection |
| `test_upload_hardening.py` | Original-extension rejection, PDF magic bytes, total-size cap |
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
