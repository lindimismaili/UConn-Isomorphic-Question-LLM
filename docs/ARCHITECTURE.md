# Architecture

How the app turns uploaded question sources into new isomorphic questions, and
where each part lives in the repository. For coding-agent onboarding, repo
conventions, and the full contributor-level tour, see [AGENTS.md](../AGENTS.md).

## Purpose

The app takes question sources and produces a fresh set of physics questions
that preserve the original reasoning pattern while changing the surface details.

## Pipeline

The registry shows the full workflow (the Evaluation node is paid, opt-in, and
skipped by default):

`Setup -> Input Guardrail -> Normalizer -> Extracter -> Content Guardrail -> Verifier -> Generator -> Latexifier -> Evaluation -> Output`

Two safety guardrails run inline: an input guardrail (content moderation plus a
jailbreak / prompt-injection check on the request and every uploaded structured
agent-input field, before any container is created) and a content guardrail.
The latter moderates and prompt-injection-screens every string field in the
complete structured extraction, while its academic/physics/is-a-question check
uses only the extracted question records. If a guardrail trips, the run ends
in a `blocked` state with a reason rather than failing as an error.

## Runtime Surfaces

The repository is split into two runtime surfaces:

- `site/backend/`: FastAPI API, workflow orchestration, evaluation, data files, and run outputs
- `site/frontend/`: React + Vite UI that talks to the backend API
- `site/dist/`: built frontend assets served by the backend when present

After raw-input screening and normalization, an Extracter cache miss gets an
isolated OpenAI container. The backend bootstraps it before uploading source
files, screens the structured extraction, deletes the source container/files,
and only then creates a clean downstream container without source attachments.
Both the server and CLI drive this same engine in
`site/backend/src/workflow/engine.py`.

## Repository Layout

- `site/backend/src/main.py` command-line workflow runner
- `site/backend/src/server.py` FastAPI app factory (CORS, lifespan, static mount)
- `site/backend/src/api/` HTTP routers by domain (health, config, files, workflows, batch) + `state.py`
- `site/backend/src/workflow/` shared engine, agent factory, payload builders, resume, SQLite store
- `site/backend/src/evaluation.py` OpenAI evals integration
- `site/backend/src/artifacts.py` workflow and agent artifact writers/readers
- `site/backend/src/container.py` explicitly owned OpenAI container runtime helpers
- `site/backend/data/` prompts, rubric, agents config, and evaluation fixtures
  (PDFs in `site/backend/data/evaluation/` are listed dynamically by the UI's
  Browse Test Files and Create Data flows — drop a file in to add an option)
- `site/backend/uploads/` uploaded source files grouped by session
- `site/backend/outputs/` timestamped workflow outputs
- `site/backend/runtime/` SQLite mirror of workflow/batch state
- `site/frontend/src/` React UI (components, hooks, utils, styles)
- `site/frontend/DESIGN.md` UI element mapping (design tokens, button/modal system)

## Workflow Stages

The pipeline has 10 registry nodes (6 agent + 2 runtime + 2 guardrail nodes):

1. Own the isolated provider scopes (the displayed setup node; actual
   container creation is lazy and follows the applicable guardrail)
2. Input guardrail: moderation + jailbreak/prompt-injection check on the request
3. Normalize the user request into workflow configuration
4. Extract source questions from the uploaded material
5. Content guardrail: moderation and injection screening over every extracted
   string field, plus an academic/physics/is-a-question check over the question
   records
6. Verify the extracted questions and answers
7. Generate isomorphic questions
8. Format the result as LaTeX and compile a PDF
9. Optionally evaluate the generated output with the paid rubric graders
10. Finalize local output files

If a guardrail trips, the run ends in a `blocked` state (distinct from an error).

The frontend mirrors these stages in the process rail, logs, and result panels.
In Dev mode, clicking a process-rail node opens its detail modal: agents get a
wide input/output inspector, while the runtime nodes open "Starting Runtime"
(runtime parameters plus the run's input files/prompt) and "Ending Runtime"
(inputs plus every output file with view/download). User and Teacher modes keep
the rail status-only. After a run completes, the results panel offers three
modals — Results (config, verified source questions with answers and distractor
logic, generated questions, evaluation summary), Evaluations (per-item judge
results with criterion drill-down), and Agents (run summary, per-agent metadata,
and artifact viewers).
