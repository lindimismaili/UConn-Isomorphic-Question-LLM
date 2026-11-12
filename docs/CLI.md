# Command-Line Usage

Generate questions, rerun stages, evaluate, and inspect runs from the terminal
instead of the web UI. The CLI drives the same workflow engine as the server.

## Subcommands

The CLI exposes argparse subcommands: `run`, `rerun-from-agent`,
`from-agent-input`, `batch`, `eval`, `compare-professor`, `improve`, `show`,
`zip`, `list` (`agents|themes|prompts|rubric|eval-files|runs`), and `prompt`.
From `site/`, run `.venv/bin/python -m backend.src.main --help` (works without
`OPENAI_API_KEY`) for the full list.

## Generating Questions

The classic flat invocation still works (leading files/flags dispatch to `run`):

```bash
cd site
.venv/bin/python -m backend.src.main "../archive/Question Data Files/PDF/Quiz_Compilation.pdf" -n 2 -i "Make questions harder"
```

On Windows, use `.venv\Scripts\python` in place of `.venv/bin/python`.

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

## Judge And Professor Agreement

Compare the rubric judge against the professor's hand grades for a batch output
folder under `site/backend/outputs/`:

```bash
cd site
.venv/bin/python -m backend.src.main compare-professor <batch-output-folder> [--source physics_1_set]
```

The folder must contain evaluation spreadsheets. See
[OUTPUTS_AND_EVALUATION.md](OUTPUTS_AND_EVALUATION.md) for the files it writes.
