---
name: agent-pipeline-changes
description: Modify or validate this repository's 10-node agent pipeline, registry metadata, models, reasoning, stages, guardrails, and container settings. Use for changes to site/backend/data/agents.json or workflow stages.
---

```toml
id = "8b380e569d72beab3da73b77447942af"
```

# Change the agent pipeline

Work from `site/`. `backend/data/agents.json` is the source of truth for the ten
registry nodes:

`setup → input_guardrail → normalizer → extracter → content_guardrail → verifier → generator → latexifier → evaluation → output`

Python reads it through `backend/src/registry.py`; the frontend reads
`GET /api/agents` through `frontend/src/hooks/useAgentRegistry.js`. Never
duplicate registry-owned node sequences, filtered sets, labels, routing maps,
or model IDs in Python or JavaScript. Stage-specific backend implementation
may use a canonical key literal for the stage it implements, provided registry
validation proves that key is part of the ten-node contract.

## Classify the change

Treat these as data-only changes unless the requested behavior says otherwise:

- model, reasoning, display metadata, turn/output-token caps, count and result
  fields;
- flow labels and stoppable/source-file/tool flags;
- themes, API title/description, guardrail thresholds and modes;
- container packages and allowed domains.

Restart the backend and reload the browser after editing because both registry
layers cache data.

Treat key renames, stage additions/removals, and input/output shape changes as
code changes. For a new LLM stage, update:

1. `backend/data/agents.json`, using canonical snake-case keys.
2. `backend/data/prompts/<key>.txt` when the stage has a prompt.
3. `backend/src/schemas.py`, registering Pydantic types in
   `AGENT_OUTPUT_TYPES` and `AGENT_INPUT_TYPES`.
4. `backend/src/workflow/payloads.py`, `engine.py`, and `resume.py`.
5. Tests for registry filtering, payloads, engine execution, resume paths, and
   frontend registry rendering.

Reuse `resolve_upstream_agent` and `finalize_agent_run`. Keep filesystem writes
off async paths through the engine's `to_thread` parameter. The external key is
always `latexifier`, never `latex`.

## Handle guardrails and prompts

Guardrail nodes execute safety checks inline even though they are excluded from
the runnable, stoppable, and LLM sets. When changing them, inspect
`backend/src/workflow/guardrails.py`, both guardrail prompts, blocked-state
tests, and the `failClosed` operational tradeoff. Preserve the current trust
boundary: injection screening covers the instruction plus every string in a
structured uploaded/resolved input, while academic/physics/on-topic/question
classification receives only extracted question records. The fixed 20,000
character instruction limit must be enforced before provider work,
idempotency hashing, and persistence, including direct engine callers.

Normal Agents SDK stages and the setup shell-task entry carry
`maxOutputTokens`; `_config.guardrails.maxOutputTokens` caps each classifier
call. Keep every value within the registry's validated 256–131,072 range.
Evaluation is separate and uses `GRADER_MAX_COMPLETION_TOKENS`.

Before editing any prompt, run:

```bash
.venv/bin/python scripts/prompt_versions.py status
```

After a prompt edit, immediately snapshot it as a prompt-only commit:

```bash
.venv/bin/python scripts/prompt_versions.py snapshot -m "<short prompt-change summary>"
```

## Preserve provider trust scopes

Do not bind the Extracter and downstream tool stages to one mutable provider
container. The engine contract is:

1. Input guardrails and Normalizer run before any container.
2. An Extracter cache miss creates an explicit extraction-only container,
   bootstraps document dependencies, and only then uploads/attaches source
   files. Cache hits create no extraction resources.
3. The resolved Extracter output passes limits and the content guardrail before
   it is cached or persisted as a fresh agent artifact.
4. The extraction container and provider file objects must retire successfully
   before downstream execution. Cleanup failure is fail-closed.
5. Verifier, Generator, and LaTeX use a fresh explicit downstream container
   created with `file_ids=[]`. Expiry recovery must also use `file_ids=[]`.

Keep SDK auto-containers disabled on production paths. Build only the agent
subset owned by each scope (`extracter` versus
`verifier`/`generator`/`latexifier`), record created resource IDs before
cancellation can interrupt bookkeeping, and drain bootstrap/heartbeat/attach
calls before deletion. Fresh, cached, replayed, and uploaded-input paths must
all re-run the applicable guardrail before a downstream container exists.

OpenAI hosted-container networking has two allowlist layers: an organization
administrator's dashboard list and the request-level `setup.allowedDomains`
(or `OPENAI_CONTAINER_ALLOWED_DOMAINS`) list. The request can only narrow the
organization list. Any domain change therefore needs a least-privilege review,
an explicit operator handoff for the organization setting, and one tiny
authorized bootstrap/compile verification; confirm the current procedure in
OpenAI's
[official shell-network guide](https://developers.openai.com/api/docs/guides/tools-shell#network-access).

## Verify

```bash
OPENAI_API_KEY= APP_ACCESS_TOKEN= .venv/bin/python -m pytest
.venv/bin/python -m ruff check .
npm -C frontend test
npm -C frontend run lint -- --max-warnings=0
npm -C frontend run build
```

Then invoke `check-agents-registry`. Tests that replace
`AGENTS_CONFIG_PATH` must clear `registry._load_raw`; frontend tests must load a
stubbed registry before rendering registry-driven components.
