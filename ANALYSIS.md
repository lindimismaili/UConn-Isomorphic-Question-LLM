# Pipeline Analysis: Generation & Evaluation

_Audit of the UConn Isomorphic Question LLM pipeline against current OpenAI best practices, including a judge↔professor agreement harness against the hand-graded Phase-1 batch ([Other/Evaluation.xlsx](../Other/Evaluation.xlsx))._

## 1. Generation pipeline walkthrough

Entry: [backend/src/main.py](../backend/src/main.py) `run_workflow()` at line 440. The pipeline is a 5-agent OpenAI Agents-SDK chain orchestrated through a shared container ([backend/src/main.py:494-505](../backend/src/main.py#L494-L505)) with PDFs uploaded once via the Files API ([backend/src/main.py:477-486](../backend/src/main.py#L477-L486), [backend/src/helpers.py:88-103](../backend/src/helpers.py#L88-L103)).

| Stage | File:lines | Output schema | Notes |
|---|---|---|---|
| Normalizer | [main.py:544-551](../backend/src/main.py#L544-L551) | `NormalizerOutput` | Parses user request |
| Extracter | [main.py:601-649](../backend/src/main.py#L601-L649) | `ExtracterOutput` | Code-interpreter reads PDFs; zero-result retry only |
| Verifier | [main.py:693-735](../backend/src/main.py#L693-L735) | `VerifierOutput` | Validates answers, adds explanations, has repair loop |
| Generator | [main.py:775-873](../backend/src/main.py#L775-L873) | `GeneratorOutput` | Creates variants, style review + repair loop |
| LaTeX/Eval | [main.py:924-1001](../backend/src/main.py#L924-L1001) | `LaTeXOutput` | Compiles in container; eval runs after |

Structured outputs are enforced via Pydantic `output_type=` on each `Agent(...)` (`agents` SDK feature, [main.py:330-404](../backend/src/main.py#L330-L404)), not via `response_format`. Reasoning effort is set per agent in [backend/data/agents.json](../backend/data/agents.json) (`"medium"` on Verifier/Generator).

### Findings — Generation

| # | Severity | Issue | Location |
|---|---|---|---|
| G1 | **High** | Generator repair loop accepts second-pass output without re-validating; a still-broken repair silently ships. | [main.py:854-873](../backend/src/main.py#L854-L873) |
| G2 | **High** | Verifier figure-bypass: `if verified.has_figure: continue` skips ALL answer checks, so a figure item with `is_correct` errors slips through. | [pipeline_validation.py:68-69](../backend/src/pipeline_validation.py#L68-L69) |
| G3 | **High** | No deterministic check that each source `distractor_logic` label appears in the generated option set — the rubric's most pedagogically important property is unaudited. | [pipeline_validation.py:110-203](../backend/src/pipeline_validation.py#L110-L203) |
| G4 | **Med** | Extractor only retries when zero items returned; a 12-question PDF returning 8 is silent. | [main.py:610-649](../backend/src/main.py#L610-L649) |
| G5 | **Med** | `normalize_question_types` silently infers MC type from presence of options instead of surfacing it as a verifier defect. | [helpers.py:165-179](../backend/src/helpers.py#L165-L179) |
| G6 | **Med** | No transient-error retry on agent calls — a single `RateLimitError` aborts the whole workflow. | [main.py](../backend/src/main.py) (all `Runner.run` sites) |
| G7 | **Med** | No cost/token instrumentation. Reasoning effort + code interpreter + repair loops are non-trivial spend with no per-run accounting. | repo-wide |
| G8 | **Low** | No prompt caching despite large stable system prompts. Per [OpenAI prompt caching docs](https://platform.openai.com/docs/guides/prompt-caching), prefix-stable content reduces cost and latency by ~50% on cache hits — applies to each agent's system prompt. | per-agent system messages |
| G9 | **Low** | `gpt-5.5` is the chosen model alias; no fallback strategy if it becomes unavailable. Validation of model availability happens at first call, not at startup. | [agents.json](../backend/data/agents.json), [helpers.py](../backend/src/helpers.py) |
| G10 | **Low** | Style-mismatch length heuristic (0.5–2.0× ratio) is only advisory; doesn't gate output. | [main.py:116-147](../backend/src/main.py#L116-L147) |

## 2. Evaluation pipeline walkthrough

Entry: [backend/src/evaluation.py](../backend/src/evaluation.py) `run_openai_output_evaluation()` at line 734. Builds JSONL of (source_text, generated_text) pairs ([evaluation.py:867-904](../backend/src/evaluation.py#L867-L904)), uploads to Files API, creates an OpenAI Eval with one `score_model` criterion per rubric entry ([evaluation.py:505-553](../backend/src/evaluation.py#L505-L553)), polls until terminal ([evaluation.py:928-936](../backend/src/evaluation.py#L928-L936)), and writes report + spreadsheet.

Aggregation in [evaluation.py:646-684](../backend/src/evaluation.py#L646-L684) drops any score `< 0` (the N/A convention) silently into nothing — the criterion just shows `items_scored=N` instead of `na_count=k`.

LaTeX criterion is deterministic and only checks artifact existence ([evaluation.py:564-597](../backend/src/evaluation.py#L564-L597)).

The grader prompt ([evaluation.py:535-550](../backend/src/evaluation.py#L535-L550)) sends both source and generated text in one shot and asks the judge to recompute distractor logic mentally — a known weak point for `distractor_structure_preservation`.

### Findings — Evaluation

| # | Severity | Issue | Location |
|---|---|---|---|
| E1 | **High** | N/A (-1) scores are filtered out of the aggregate with no `na_count`/`na_reason` reported. Stakeholders cannot distinguish "judged N/A by all 10 items" from "judged pass by 0 items". | [evaluation.py:663-682](../backend/src/evaluation.py#L663-L682) |
| E2 | **High** | No reconciliation that `len(output_items) == len(dataset_items)`. If the OpenAI eval silently drops a malformed item, the report shows the smaller denominator without flagging coverage. | [evaluation.py:952-962](../backend/src/evaluation.py#L952-L962) |
| E3 | **High** | **No judge↔professor comparison anywhere.** The professor's hand-graded grid in [Other/Evaluation.xlsx](../Other/Evaluation.xlsx) is never loaded; reliability of LLM-judge scores is unmeasured. _Addressed by new agreement harness — §4._ |
| E4 | **Med** | LaTeX check is artifact-existence only — a present `.tex` that failed to compile cleanly still passes if a `.pdf` exists. Should parse `.log` for `! LaTeX Error` and `Undefined control sequence`. | [evaluation.py:564-597](../backend/src/evaluation.py#L564-L597) |
| E5 | **Med** | `distractor_structure_preservation` grader conflates label-matching with value-recomputation in a single call, raising hallucination risk on the criterion most likely to drive disagreement with the professor. | [evaluation.py:535-550](../backend/src/evaluation.py#L535-L550) |
| E6 | **Med** | No seed/temperature control surfaced for the `score_model` grader; double-running the same eval can produce different grids. No flip-rate monitoring. | [evaluation.py:505-553](../backend/src/evaluation.py#L505-L553) |
| E7 | **Low** | Grader instruction "Apply the rules in the rubric guidance literally" ([evaluation.py:540](../backend/src/evaluation.py#L540)) is vague — drop or replace with a checklist. | [evaluation.py:535-550](../backend/src/evaluation.py#L535-L550) |
| E8 | **Low** | Tests cover contract shape ([backend/tests/test_evaluation_contract.py](../backend/tests/test_evaluation_contract.py)) but not aggregation edge cases (all-NA, mixed -1/0/1, coverage mismatch). | tests |

## 3. OpenAI best-practice checklist (current SDK ≥ 2.0)

| Feature | Status | Action |
|---|---|---|
| Agents SDK with structured outputs | ✓ Used correctly via `output_type=` | — |
| Files API for source PDFs | ✓ | — |
| Containers (code-interpreter sessions) | ✓ Shared container with 20-min expiry | — |
| Evals API (`score_model` criteria) | ✓ | Add coverage assert (E2), na reporting (E1) |
| Prompt caching | ✗ Not exploited | Move large stable rubric/system prefix to top of message so prefix-cache hits — see G8 |
| Reasoning controls | ✓ `Reasoning(effort="medium")` on Verifier/Generator | — |
| Retries / backoff on transient errors | ✗ Manual repair loops, no SDK retry wrapper | Wrap `Runner.run` in `tenacity` retry on `RateLimitError` / `APIConnectionError` / `InternalServerError` only |
| Cost/token logging | ✗ | Persist `usage_log.jsonl` per run |
| Determinism (seed) | ✗ Not surfaced for `score_model` | Record + persist when SDK supports it |
| Streaming | n/a (batch workflow) | — |

## 4. The overhaul

Each finding above is addressed by one of the deliverables shipped in this change:

- **Judge↔Professor agreement harness** ([backend/src/judge_agreement.py](../backend/src/judge_agreement.py), [Other/compare_to_professor.py](../Other/compare_to_professor.py)) — closes E3. Loads the professor's xlsx, reuses [Other/createXLSX.py](../Other/createXLSX.py)'s `parse_cell` to load the judge grid, computes per-criterion raw agreement + Cohen's κ + confusion matrix, and writes `judge_agreement_report.json` plus an item-level `judge_disagreements.csv` for inspection. Also adds an "Agreement" sheet to `compiled.xlsx`.
- **`pipeline_validation.py` tightening** — adds `validate_distractor_logic_preservation` (G3) and tightens the figure path (G2) to still require non-empty `distractor_logic` and a rationale.
- **`evaluation.py` reporting** — adds `na_count`/`scored_count`/`na_rate` per criterion (E1), `coverage_mismatch` (E2), LaTeX log parsing (E4), and a `--seed-grader` switch placeholder.
- **Repair loop hardening** ([backend/src/main.py](../backend/src/main.py)) — wraps the generator repair in a bounded re-validation loop (G1).
- **Tests + CI** — new pytest modules covering all of the above, plus `.github/workflows/ci.yml` and `pyproject.toml` with ruff config.

See the plan file for full deliverable details: `/Users/lindimismaili/.claude/plans/do-a-full-analysis-mutable-whisper.md`.

## 5. How to use the new agreement harness

```bash
# After generating a batch:
python Other/createXLSX.py Other/20260516T034016014146Z-other-phase1-eval-batch
python Other/compare_to_professor.py Other/20260516T034016014146Z-other-phase1-eval-batch

# Outputs:
#   <batch>/XLSX/judge_agreement_report.json
#   <batch>/XLSX/judge_disagreements.csv
#   <batch>/XLSX/compiled.xlsx (Agreement sheet appended)
```

Per-criterion κ interpretation (Landis & Koch): <0.2 poor · 0.2–0.4 fair · 0.4–0.6 moderate · 0.6–0.8 substantial · >0.8 almost perfect. Any criterion landing below 0.4 should be considered for grader-prompt rework before being trusted in automated reports.
