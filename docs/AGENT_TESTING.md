# Agent Testing Protocols

This repo already supports partial workflow runs through the `Input To Agent` developer tool in the UI. The fixtures in `backend/data/testing/` make that path repeatable so each agent can be tested without restarting from the beginning every time.

These checks are developer-only. They do not run inside the main workflow and do not change runtime behavior.

## Fixture Layout

- `backend/data/testing/prompts/normalizer_style_prompt.txt`
- `backend/data/testing/sources/verbose_open_ended_source.txt`
- `backend/data/testing/sources/mixed_question_types_source.txt`
- `backend/data/testing/agent_inputs/verifier/verbose_open_ended_and_multipart.json`
- `backend/data/testing/agent_inputs/generator/verbosity_preservation_open_ended.json`
- `backend/data/testing/agent_inputs/latex/mixed_question_types.json`
- `backend/data/testing/agent_inputs/evaluation/mixed_question_types.json`

Optional developer commands:

```bash
./.venv/bin/python -m backend.src.validate_test_fixtures
./.venv/bin/python -m unittest backend.tests.test_style_testing
./.venv/bin/python -m backend.src.check_generator_style backend/outputs/<output-folder>
```

## UI Workflow

1. Start the app.
2. Paste the desired prompt into the main prompt box.
3. Open `Input To Agent`.
4. Choose the target agent.
5. Upload the matching fixture source file or saved agent input.
6. Start the workflow and inspect the selected agent card plus the saved `agent_outputs/<agent>/` artifacts.

## Protocols By Agent

### 1. Normalizer

Use:

- Prompt: `backend/data/testing/prompts/normalizer_style_prompt.txt`
- Source file: `backend/data/testing/sources/verbose_open_ended_source.txt`
- Start agent: `normalizer`

Check:

- `new_questions_per_question=2`
- `difficulty=""`
- `theme=""`
- `title=""`
- `notes` preserves the request to keep open-ended questions similarly verbose

Failure signals:

- Difficulty or theme gets invented
- The verbosity/style instruction is dropped from `notes`

### 2. Extracter

Use:

- Source file: `backend/data/testing/sources/mixed_question_types_source.txt`
- Start agent: `extracter`

Notes:

- Starting from `extracter` bypasses prompt normalization and uses the default empty normalizer config. That is intentional for extractor-only testing.

Check:

- Question `1` is `open_ended`
- Question `2` is `multi_part` with two parts
- Part `2b` is `multiple_choice` with four options
- Question `3` sets `has_figure=true`
- No questions are dropped

Failure signals:

- Multi-part question is flattened into separate parent questions
- Figure reference is missed
- Open-ended question is misclassified as multiple choice

### 3. Verifier

Use:

- Agent input: `backend/data/testing/agent_inputs/verifier/verbose_open_ended_and_multipart.json`
- Start agent: `verifier`

Check:

- Open-ended question `1` receives a non-empty `answer`
- Multi-part question `2a` receives a non-empty `answer`
- Multi-part question `2b` marks exactly one correct option and fills explanations
- Figure question `3` stays unchanged

Failure signals:

- Any question disappears
- Figure question is solved instead of preserved
- MC explanations are still blank

### 4. Generator

Use:

- Agent input: `backend/data/testing/agent_inputs/generator/verbosity_preservation_open_ended.json`
- Start agent: `generator`

Primary purpose:

- Verify that verbose open-ended sources stay verbose, narrative, and explanation-oriented after generation.
- The workflow now performs that style review with an agent pass, not with local runtime validation code.
- If you want a local regression signal after a run, use `check_generator_style` against that output folder.

Check:

- Source `1` remains `open_ended`
- Generated stem for source `1` stays multi-sentence and close in length to the source
- Source `2` remains `multi_part`
- Source `3` becomes a skipped placeholder with `skip_reason="Question references a figure"`
- The generated output still preserves ids, source_ids, and part structure after the generator's review pass

Failure signals:

- Verbose source `1` is compressed into a short directive
- Part structure is lost
- Figure question is generated instead of skipped

### 5. LaTeX Formatter

Use:

- Agent input: `backend/data/testing/agent_inputs/latex/mixed_question_types.json`
- Start agent: `latex`

Check:

- `isomorphic_questions.tex` is written
- `isomorphic_questions.pdf` is written
- Open-ended, multi-part, and skipped-question blocks all appear in the document

Failure signals:

- PDF compilation fails
- Skipped question is omitted
- Multi-part question is flattened or malformed in the PDF

### 6. Evaluation

Use:

- Agent input: `backend/data/testing/agent_inputs/evaluation/mixed_question_types.json`
- Start agent: `evaluation`

Check:

- `evaluation_outputs/evaluation_report.json` exists
- `evaluation_outputs/evaluation_dataset.jsonl` exists
- `evaluation_outputs/evaluation_items.jsonl` exists
- The report contains scored criteria, not an empty shell

Failure signals:

- Evaluation output files are missing
- Report creation fails because the LaTeX artifact cannot be reconstructed

## Recommended Regression Order

1. Run the Generator protocol after any prompt/schema change affecting question wording
2. Run the Extracter and Verifier protocols after parser/schema changes
3. Run the LaTeX and Evaluation protocols after output-format changes
4. Optionally run `check_generator_style` on the resulting output folder for a local regression report

## Why These Fixtures Exist

- Early-stage source fixtures test extraction/classification behavior directly.
- Downstream agent-input fixtures let you resume from a specific stage using the existing UI.
- The generator fixture is intentionally centered on a verbose open-ended source because that is the regression most likely to recur.
