---
name: grader-review
description: Review LLM-grader evaluation output(s) and surface ranked, PROPOSE-ONLY improvements to the generator/verifier prompts and rubric. Use after an evaluation run (or a batch of runs) when you want to know WHY questions failed and what hand-curated file to consider editing, or when validating the grader against labeled data with a Cohen's kappa gate. Never auto-applies edits.
---

# Grader review (meta-evaluator)

Runs the offline **meta-evaluator** over one or more evaluation output dirs. It
mines each failing item's `[TAG]` failure code + grader rationale, folds in the
deterministic pre-judge checks, and emits **ranked proposals** mapping the
dominant failure tags to specific hand-curated files.

**PROPOSE-ONLY — never auto-apply.** Prompts (`site/backend/data/prompts/*.txt`)
and the rubric (`site/backend/data/rubric.json`) are hand-curated and
prompt-versioned via `scripts/prompt_versions.py`. This skill only *proposes*
edits for a human to review, adapt, and snapshot. Do not edit those files as part
of running this skill.

## When to use

- After an evaluation run: `site/backend/outputs/<ts>/evaluation_outputs/`.
- Over a phase-1 eval batch: each `Other/<ts>-...-eval-batch/<pdf>/evaluation_outputs/`.
- When you have a labeled calibration dataset (graded by the latest grader, or
  professor labels) and want a per-criterion Cohen's kappa PASS/FAIL gate.

## 1. Run it (unlabeled mode)

From `site/`:

```bash
.venv/bin/python -m backend.src.tools.meta_evaluator \
  --eval-dir outputs/<ts>/evaluation_outputs
```

Batch (repeat `--eval-dir`, results aggregate across dirs):

```bash
.venv/bin/python -m backend.src.tools.meta_evaluator \
  --eval-dir ../Other/<ts>-...-eval-batch/<pdfA>/evaluation_outputs \
  --eval-dir ../Other/<ts>-...-eval-batch/<pdfB>/evaluation_outputs
```

It prints a markdown summary to stdout and writes
`meta_evaluation_report.json` next to the first `--eval-dir` (override with
`--json-out <path>`). Each run also appends one record to the experiment ledger
`site/backend/runtime/meta_eval_ledger.jsonl` (skip with `--no-ledger`).

## 2. Read the output and present it

1. Read the **Per-criterion failure analysis** table: which criteria fail most,
   and each criterion's `dominant_tag`.
2. Read the **Ranked proposals** list. Each proposal carries `target_file`,
   `change_type`, `criterion`, `rationale`, `evidence` (with concrete
   `failing_source_ids`), `expected_metric_moved`, `suggested_text`,
   `confidence`, `corroborating_signal`, and `rank_score`.
3. Present the top proposals to the human. For each, state the target file, the
   rationale, and the suggested edit. Do **not** apply anything.

Every proposal cites a corroborating signal (`failure_tag`, `deterministic_check`,
or `kappa_gap`) — the tool filters out any proposal whose only effect would be
raising the judge's pass rate (reward-hacking guard).

## 3. Labeled / kappa-gate mode (optional)

When a calibration dataset is available (see
`site/backend/data/calibration/README.md` for the format), pass it to get a
per-criterion Cohen's kappa gate and calibration proposals targeting the rubric:

```bash
.venv/bin/python -m backend.src.tools.meta_evaluator \
  --eval-dir outputs/<ts>/evaluation_outputs \
  --labels data/calibration/<name>.jsonl \
  --kappa-floor 0.4
```

Any criterion with `kappa < kappa-floor` reports **FAIL** in the Kappa gate
table and gets a `data/rubric.json:<criterion>` calibration proposal (a low-kappa
criterion means the *grader/rubric* disagrees with reference labels — a grader
problem, not a generator problem). The process exits non-zero when the overall
gate FAILs, so it can be wired into CI.

## 4. Applying an accepted proposal (human, manual)

If the human decides to apply a proposal:

1. Check current prompt status: from `site/`,
   `python scripts/prompt_versions.py status`.
2. Hand-edit the target file with the (adapted) `suggested_text`.
3. Snapshot prompt edits immediately:
   `python scripts/prompt_versions.py snapshot -m "<summary>"`.
   (rubric.json is versioned via normal git commits.)
4. Re-run the evaluation, then re-run this skill and confirm the targeted
   `expected_metric_moved` improved without regressing other criteria (compare
   `meta_eval_ledger.jsonl` entries across the change).

## Notes

- The meta-evaluator reads `evaluation_report.json` and
  `evaluation_agents.jsonl` (falls back to the report's `criterion_scores` block
  when the raw items file is absent — then proposals lack per-item source ids).
- Failure tags come from the fixed taxonomy in `backend/src/evaluation.py`
  (`FAILURE_TAGS`); the failure-tag -> file mapping lives in
  `backend/src/tools/meta_evaluator.py` (`TAG_TARGET_MAP`).
- Runs that predate the `[TAG]` taxonomy attribute every failure to `OTHER`
  (a `review_manually` proposal) — that is expected, not a bug.
