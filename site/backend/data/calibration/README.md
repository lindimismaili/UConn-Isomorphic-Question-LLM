# Grader calibration datasets

Versioned, propose-only calibration data consumed by the meta-evaluator
(`backend/src/tools/meta_evaluator.py`, labeled mode `--labels`). These datasets
let the meta-evaluator compute a per-criterion **Cohen's kappa gate** comparing a
reference label against the grader's label, WITHOUT relying on live human labels
at run time.

**No real data files live here yet** — this README only defines the format. Drop
`*.jsonl` files here (git-tracked, like the rest of `data/`) when you have graded
items to calibrate against.

## Purpose

The meta-evaluator's default (unlabeled) mode reasons from grader rationales +
deterministic checks + self-consistency. When a labeled dataset is supplied, it
additionally computes, per criterion:

- Cohen's kappa (reusing `backend/src/tools/judge_agreement.py::cohen_kappa`),
- raw agreement,
- grader-inflated count (reference=fail, grader=pass) and grader-missed count
  (reference=pass, grader=fail),
- a **PASS/FAIL gate** against `--kappa-floor` (default 0.4), requiring at
  least `--min-kappa-samples` non-N/A comparisons per criterion (default 5).

A criterion that FAILs the gate yields a `data/rubric.json:<criterion>`
calibration proposal — a low-kappa criterion means the *grader/rubric* disagrees
with the reference, i.e. a grader problem, not a generator problem.
Insufficient or all-N/A evidence also fails the gate but does **not** produce a
rubric-change proposal; gather more independent labels instead.

## File format

One JSON object per line (JSONL). One record = one graded (item, criterion) pair.

```json
{"source_id": "3", "criterion": "physics_correctness", "human_or_reference_label": 1, "grader_label": 1, "dataset_version": "physics1-v1"}
{"source_id": "3", "criterion": "distractor_structure_preservation", "human_or_reference_label": 0, "grader_label": 1, "dataset_version": "physics1-v1"}
{"source_id": "4", "criterion": "numerical_variation", "human_or_reference_label": "na", "grader_label": 1, "dataset_version": "physics1-v1"}
```

### Fields

| field | type | meaning |
|---|---|---|
| `source_id` | string/int | the item's source question id (matches the eval item's `source_id`) |
| `criterion` | string | rubric criterion name, e.g. `physics_correctness` (must match `rubric.json` `criteria[].name`) |
| `human_or_reference_label` | `0` \| `1` \| `-1`/`"na"` | the independently reviewed reference label: 1=pass, 0=fail, -1/`"na"`=N/A. Prefer professor labels; a grader-produced label is not independent validation of that grader. |
| `grader_label` | `0` \| `1` \| `-1`/`"na"` | the label the grader-under-test produced for this pair |
| `dataset_version` | string | version tag for this calibration set (recorded in the report + experiment ledger) |

### Label normalization

Labels are normalized to `0` / `1` / `"na"` before scoring (see
`meta_evaluator._norm_label`). Accepted forms:

- pass: `1`, `"1"`, `"pass"`, `"passed"`, `"true"`, `"yes"`, `true`
- fail: `0`, `"0"`, `"fail"`, `"failed"`, `"false"`, `"no"`, `false`
- N/A: `-1`, `"na"`, `"n/a"`, `null`, `""`

**N/A handling:** a pair where either side is N/A is excluded from kappa and
counted under `n_na_excluded` (mirrors `judge_agreement`), so a grader is not
penalized for a declared-N/A the reference also declared.

## Versioning

- `dataset_version` is a first-class key. Keep it stable for a given labeled set
  and bump it (e.g. `physics1-v2`) when you add/relabel items. It flows into
  `meta_evaluation_report.json` and each `meta_eval_ledger.jsonl` entry so a
  measured kappa can be traced to the exact `(prompt_version, rubric_version,
  dataset_version)` triple.
- Prefer a stable `source_id`-based join over spreadsheet row order.
- Reports record `prompt_version` and `rubric_version` as SHA-256 content
  hashes, including dirty working-tree content. This makes a result traceable
  even before prompt/rubric edits are committed.
- To seed a dataset from the professor workbook
  (`backend/data/professor_comparison/Evaluation.xlsx`), export
  each (source_id, criterion) cell as one record with `human_or_reference_label`
  = the professor cell and `grader_label` = the matching grader result; keep the
  join exact (error loudly on mismatch rather than falling back to row order).

## Usage

From `site/`:

```bash
.venv/bin/python -m backend.src.tools.meta_evaluator \
  --eval-dir backend/outputs/<ts>/evaluation_outputs \
  --labels backend/data/calibration/<name>.jsonl \
  --kappa-floor 0.4 \
  --min-kappa-samples 5
```

The command exits non-zero when the overall kappa gate FAILs (CI-friendly).
