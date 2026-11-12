# Outputs And Evaluation

Where each run writes its files, how the optional rubric evaluation works, and
what a run costs in API calls. Read this when looking for a run's results,
interpreting evaluation scores, or estimating spend.

## Run Outputs

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

Batch create-data runs also write batch summaries and per-file workflow folders
under `site/backend/outputs/`. Running `compare-professor` on a batch adds
`XLSX/compiled.xlsx`, `XLSX/judge_agreement_report.json`, and
`XLSX/judge_disagreements.csv` to that batch folder.

## Evaluation

The evaluation step uses the rubric in `site/backend/data/rubric.json` and
writes scored results into the output folder for the run. Those files are the
primary record of how the generated questions were judged.

> [!WARNING]
> The OpenAI Evals API used here became read-only on **October 31, 2026** and
> shuts down on **November 30, 2026**, per the
> [official OpenAI deprecations page](https://developers.openai.com/api/docs/deprecations).
> Rubric evaluation needs a verified replacement path. Generation still works
> with evaluation disabled.

## Costs

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
