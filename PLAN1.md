# Repo hardening: CLI parity, test coverage, redundancy cleanup, OpenAI-practices audit, and `Other/` integration

## Context

The user wants this repo audited and tightened across several axes: full CLI parity with the site, adequate test coverage, no redundant/duplicated logic, current OpenAI SDK practices, `Other/`'s eval-comparison tooling folded into `site/backend` and exposed on the site, and a real parallel pipeline run across the bundled physics source sets.

Investigation (Explore + direct file reads) found the scope is much smaller than it first appeared, because two of the six asks are **already fully implemented**:

- **"Run the pipeline in parallel across quantum/short/verbose/physics1/physics2"** — `site/backend/data/evaluation/` already contains exactly those 5 PDFs, and `main.py batch` (== `POST /api/create-data/start`, == the frontend's "Create Data" flow) already runs the full 10-node pipeline over every file in that folder concurrently, gated by `BATCH_WORKFLOW_CONCURRENCY` (default 2). CLI, API, and frontend all call the same `run_create_data_batch_async`.
- **"Eval on existing generated datasets" / "comparison tool feel"** — `main.py eval --runs/--pairs` (== `POST /api/eval-batch/start`, == the frontend's `BatchEvalModal`/`useBatchEval`) already runs eval-only passes over prior output folders or uploaded source/generated PDF pairs, via `site/backend/src/eval_pipeline.py`. This is *already* functionally equivalent to what `Other/runEvals.py` does — `runEvals.py` is redundant, not missing functionality.

So this plan does **not** rebuild batch/parallel infrastructure. It focuses on the one real gap (professor-comparison tooling), plus cleanup, testing, and one substantial risk finding from the OpenAI-practices audit. User confirmed: port-and-retire `Other/` (delete the whole folder once ported), research-and-fix-clear-wins on OpenAI practices, and actually execute the 5-set parallel batch for real as the final step.

## Phase 1 — Consolidate duplicated path-resolution logic

Three near-identical "resolve a relative path safely under a root dir" implementations exist:
- `site/backend/src/api/state.py:923-941` `_resolve_output_folder_label(label)` (HTTP twin, raises `HTTPException`)
- `site/backend/src/main.py:125-143` `_resolve_output_folder(label)` (CLI twin, raises `ValueError`/`FileNotFoundError`)
- `site/backend/src/main.py:154-173` `_resolve_test_files(rel_paths)` vs. the inline `testFiles` loop in `site/backend/src/api/workflows.py:96-114` (`start_workflow`)

Add a pure resolver to `state.py` that both twins wrap:

```python
class OutputFolderError(ValueError):
    def __init__(self, message: str, *, not_found: bool = False):
        super().__init__(message)
        self.not_found = not_found

def _resolve_output_folder_path(label: str) -> Path:
    ...  # validation + _is_under_directory(..., OUTPUTS_DIR) check, raises OutputFolderError
```

`_resolve_output_folder_label` becomes a thin `HTTPException`-raising wrapper; `main.py:_resolve_output_folder` becomes a thin `ValueError`/`FileNotFoundError`-raising wrapper — **preserve today's exact exception types** so existing test patches (`test_cli.py`) and HTTP status codes (`test_api_routes.py`) don't change. Same pattern for test-file resolution: a shared `_resolve_evaluation_fixture_paths(rel_paths, *, strict)` in `state.py`, with `strict=False` for `workflows.py` (today's silent-skip behavior) and `strict=True` for the CLI (today's raise-on-first-bad-entry behavior).

Zero external behavior change — pure internal dedup.

## Phase 2 — Close test coverage gaps

No test file currently exists for `site/backend/src/api/batch.py`, `site/backend/src/api/eval_batch.py`, or `site/backend/src/eval_pipeline.py` — exactly the concurrency-critical batch machinery. No CLI-level test exists for the `batch`, `eval`, or `improve` subcommands either (`test_cli.py` covers `run`/`rerun`/`from-agent-input`/`list`/`prompt`/`show`/`zip` only).

Add, following the existing mocking conventions in `test_cli.py` (`patch.object`/module-attribute patching) and `test_api_routes.py` (`TestClient`):

- `site/backend/tests/test_api_batch.py` — `POST /api/create-data/start` + status/result/stop; assert 503 with no client, entry shape, concurrency respects a patched `BATCH_WORKFLOW_CONCURRENCY`, stop cancels non-terminal entries.
- `site/backend/tests/test_api_eval_batch.py` — both `mode=prior_runs` (404 on missing run) and `mode=upload` (413 oversized, entries from uploaded PDFs).
- `site/backend/tests/test_eval_pipeline.py` — unit tests for `eval_pipeline.py`'s pure functions (`_extraction_payload`, `question_to_generated_item`, `build_eval_final_result`), `Runner.run` mocked at the boundary.
- `test_cli.py` additions: `BatchCommandTests`, `EvalCommandTests` (both `--runs` and `--pairs` branches), `ImproveCommandTests`.

## Phase 3 — `main.py list runs` parity fix

`_cmd_list`'s `runs` branch does a bare `OUTPUTS_DIR.rglob` + mtime print, while `GET /api/eval-batch/outputs` (`_list_output_runs` in `state.py`) already reports question count + existing score. Before wiring, verify `_list_output_runs`'s scan depth actually covers the same run-folder layout the CLI's `rglob` walk covers (`_list_output_runs` uses `OUTPUTS_DIR.iterdir()`, not recursive) — if there's a real depth mismatch, extract a shared `_run_summary(run_dir) -> dict` helper used by both instead of just calling `_list_output_runs` from the CLI. Otherwise, replace the CLI branch with a direct call to `_list_output_runs`, printing label + question count + score per run.

## Phase 4 — Professor-comparison tool: port, wire up, retire `Other/`

`Other/compare_to_professor.py` (kappa/agreement vs. `Other/Evaluation.xlsx`, the professor's hand-graded grid) and `Other/createXLSX.py` (compiles a batch's per-PDF `evaluation_spreadsheet.csv` files into one workbook) are not wired into `site/backend` at all today, and both are currently **broken** — their hardcoded `REPO_ROOT` still points above `site/`, a leftover from before the `site/` reorg. `judge_agreement.py` (the metric engine both scripts call into) is already correct, portable, and tested — no changes needed there.

**4a. New module — `site/backend/src/tools/professor_comparison.py`**

Port `createXLSX.py`'s `create_xlsx(batch_dir)` (generic — works on any batch dir whose entries have `evaluation_outputs/evaluation_spreadsheet.csv`, which both create-data and eval-batch output folders already produce) and `compare_to_professor.py`'s `write_agreement_sheet` + orchestration into `run_professor_comparison(batch_dir, professor_xlsx, *, source_stem="") -> dict`. Two fixes while porting:
- Replace the broken hardcoded `RUBRIC_PATH` / reimplemented `load_rubric()` with the existing `rubric_criteria()` accessor from `site/backend/src/evaluation.py` — don't duplicate rubric parsing.
- Move the hardcoded `CONCEPTUAL_IDS`/`ABSTRACT_IDS`/`NUMERICAL_IDS` (professor's manual physics_1-specific classification) into a new hand-curated data file `site/backend/data/professor_comparison/source_classification.json`, keyed by source-PDF stem. Unrecognized/blank `source_stem` degrades gracefully to `"na"` averages (the existing `avg()` logic already returns `"na"` for an empty id set — no extra code needed).
- Drop `newest_batch()` — always take an explicit batch dir.

**4b. CLI — `main.py compare-professor <batch> [--professor PATH] [--source STEM]`**

New subcommand following the existing `p_eval`/`_cmd_eval` pattern (`main.py:783-793`/`454-566`), reusing the consolidated `_resolve_output_folder` from Phase 1. Mirrors `compare_to_professor.py`'s printed summary + exit codes (2 = missing workbook, 3 = comparison failure).

**4c. API — one generic route**, not one per batch type: `POST /api/outputs/{label:path}/compare-professor` in a new `site/backend/src/api/professor_comparison.py`, reusing `_resolve_output_folder_label` (Phase 1) to resolve the label safely. Both create-data and eval-batch output folders live under `OUTPUTS_DIR` in the same shape, so one route serves both — no need for the frontend or backend to know which batch type produced the folder. Response: `{outputFolder, summary: {mean_kappa, mean_raw_agreement, criteria_with_poor_kappa}, criteria: {...}, xlsxPath}`. Register the router in `server.py`.

**4d. Frontend** — new `site/frontend/src/components/modals/CompareProfessorPanel.jsx`, composed only from existing `ui/` primitives (`Button`, `Banner`, `KeyValueTable`) per `DESIGN.md`'s compose-not-raw-elements rule. A one-shot POST + local `useState` (no new hook needed — this isn't a poll loop like `useBatchEval`/`useCreateData`). Mount it inside the terminal-status "completed summary" block of both `BatchEvalModal.jsx` and `CreateDataModal.jsx`, passing `outputFolder`.

**4e. Ground truth data** — copy `Other/Evaluation.xlsx` → `site/backend/data/professor_comparison/Evaluation.xlsx` (byte-identical, hand-curated, do not alter content) alongside the new `source_classification.json`.

**4f. Tests** — `site/backend/tests/test_professor_comparison.py` (module-level: `create_xlsx` sheet/average correctness, `run_professor_comparison` end-to-end writes + `Agreement` sheet replace-not-duplicate behavior), a `CompareProfessorCommandTests` class in `test_cli.py`, and API route coverage (404 on bad label / missing workbook, 200 with the summary shape).

**4g. Delete `Other/` entirely** (git rm -r), only after the above is ported and `test_professor_comparison.py` passes against the copied fixtures:
- `runEvals.py`, `createXLSX.py`, `compare_to professor.py` → ported into `site/backend/src/tools/professor_comparison.py` + `main.py` + the new API route.
- `Evaluation.xlsx` → copied to `site/backend/data/professor_comparison/`.
- `physics_1_set.pdf` → byte-identical duplicate of `site/backend/data/evaluation/physics_1_set.pdf`, no migration needed.
- `Phase_1/`, `Cleaned XLSX/`, the 8 timestamped `*-other-phase1-eval-batch/` folders → purely generated historical artifacts of the now-retired `runEvals.py`, deleted with no migration.

**4h. Update CLAUDE.md** — repoint the "Tooling / analysis" section, the judge-agreement CLI snippet, and the "Important Files" row that reference `Other/` paths to the new `site/backend/src/tools/professor_comparison.py` / `main.py compare-professor` / `data/professor_comparison/` locations.

## Phase 5 — OpenAI SDK/practices audit findings

Researched against current OpenAI docs (package pins: `openai>=2.0.0`, `openai-agents>=0.15.0`, both open floors — current PyPI latest is `openai` 2.44.0 / `openai-agents` 0.17.7). Five of six areas checked are **current and correct as-is** — no code changes needed:

- Agents SDK usage (`Runner.run`/`run_streamed`, `RunConfig`, `output_type`, per-agent `max_turns`) — no breaking changes between the pinned floor and latest that affect this repo's patterns.
- Code Interpreter / shared-container tool usage (`container.py`, `workflow/agents.py`) — the `container: {"type": "auto", "file_ids": [...]}` and `{"type": "shell", "environment": {"type": "container_reference", ...}}` shapes match current docs exactly, including the 20-minute reuse window.
- Moderation model `omni-moderation-latest` (`guardrails.py`) — still the current recommended model.
- Exception handling (`MaxTurnsExceeded`/`ModelBehaviorError`/`ModelRefusalError` in `engine.py`) — still the correct, non-renamed exception surface.
- Reasoning-effort construction (`ModelSettings(reasoning=Reasoning(effort=...))`) — matches current SDK docs exactly.

**One real, time-boxed risk found, not a "clear win" (no code change proposed here):** OpenAI's **Evals platform — which `site/backend/src/evaluation.py`'s entire grading pipeline is built on (`client.evals.create`, `score_model` graders, seeded reasoning effort, `FAILURE_TAGS` mining) — goes read-only October 31, 2026 and shuts down November 30, 2026**, per current OpenAI docs. There is no published migration guide yet. This needs a deliberate follow-up plan closer to the deprecation date (or as soon as OpenAI publishes a "Datasets"-based migration path) — flagging it now in CLAUDE.md as a tracked risk is the only action this plan takes; do not attempt to rearchitect `evaluation.py` yet, since there's nothing concrete to migrate to.

Optional low-risk hygiene, not required: bump `requirements.txt`'s `openai`/`openai-agents` floors to current latest and re-run the test suite.

**Action for this plan:** add a short note to `CLAUDE.md`'s "Evaluation System" section flagging the Nov 30 2026 Evals shutdown as a tracked risk with no current migration path.

## Phase 6 — Execute the real parallel batch run

Once Phases 1-5 land and the full test suite is green, run `main.py batch` (equivalently, the Create Data flow) for real against all 5 files in `site/backend/data/evaluation/` (quantum_set, short_set, verbose_set, physics_1_set, physics_2_set). This is a real OpenAI API run — costs money, takes real wall-clock time, produces real output folders under `site/backend/outputs/`. Confirm `OPENAI_API_KEY` is set, run it (foreground or backgrounded with progress monitoring), and report the resulting batch summary (per-file status, output folders, question counts, eval scores) back to the user. No special instructions applied unless the user wants specific ones at execution time.

## Verification

- `cd site && .venv/bin/python -m pytest` — full backend suite green, including all new test files.
- `cd site && .venv/bin/python -m ruff check .` — lint clean.
- `npm -C site/frontend test` and `npm -C site/frontend run lint` — frontend clean (new `CompareProfessorPanel.jsx` + its usage in both modals).
- Manually run `main.py compare-professor <a-real-or-fixture-batch-dir>` once against a real prior batch output to confirm the ported logic produces a sane `compiled.xlsx` + agreement report before deleting `Other/`.
- Confirm `git status` shows `Other/` fully removed and `site/backend/data/professor_comparison/` present with the two new files.
- Phase 6: after `main.py batch` completes, inspect `outputFolder`/`batch_summary.json` and report completed/failed counts per file.
