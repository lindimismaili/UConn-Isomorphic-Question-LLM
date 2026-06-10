---
name: agent-pipeline-changes
description: Modify the agent pipeline in this repo — change a stage's model/reasoning/labels/metadata, rename a stage, add or remove a stage, or change container bootstrap config. Use whenever a task touches backend/data/agents.json or asks to add/rename/reorder workflow agents, because the data-only vs needs-code boundary and the cache/restart rules are easy to get wrong.
---

# Changing the agent pipeline

`backend/data/agents.json` is the single source of truth for all 8 pipeline
stages (`setup → normalizer → extracter → verifier → generator → latexifier →
evaluation → output`) plus `_config` (themes, API title/description). Backend
code reads it through `backend/src/registry.py` (lazy, `lru_cache`'d);
the frontend through `GET /api/agents` via
`frontend/src/hooks/useAgentRegistry.js` (module-level cache).

**Never hardcode agent keys, order, or display names** in Python or JS.
Backend: import from `backend.src.registry`. Frontend: `useAgentRegistry()`
in components, `getDerivedRegistry()` in reducers/callbacks.

## Decide: data-only or code change?

**Data-only (edit agents.json, nothing else):**
- model, reasoning effort, display `name`, `description`
- `countLabel` / `countField` (log label + output array used for counts)
- `inputFrom` / `outputTo` flow display names
- `cliDescription`, `isStoppable`, `acceptsSourceFiles`, `aliases`
- `_config.themes`, `apiTitle`, `apiDescription`
- `setup.allowedDomains`, `setup.containerPythonPackages`

After editing: **restart the backend** (registry is cached per process) and
**reload the page** (frontend caches `/api/agents` at module level).

**Needs code (agents.json alone is not enough):**
- Renaming an agent **key** (not just its display name)
- Adding or removing a stage
- Changing what a stage's input/output payload looks like

## Adding a new LLM stage — checklist

1. `agents.json`: new entry with the right `sequence` (re-number downstream
   stages), `kind: "agent"`, `isLLM`, `hasPrompt`, flags. Keys are snake_case;
   the LaTeX agent's key is `latexifier` (never `latex` in external surfaces).
2. `backend/data/prompts/<key>.txt` — instructions (data file: create only
   when the user asked for the stage).
3. `backend/src/schemas.py` — Pydantic input/output models; register in
   `AGENT_OUTPUT_SCHEMAS` / the input map.
4. `backend/src/workflow/payloads.py` — `build_<key>_input` builder.
5. `backend/src/workflow/engine.py` — stage block. Reuse
   `resolve_upstream_agent` / `finalize_agent_run`; never import FastAPI types;
   file writes go through the engine's `to_thread` parameter.
6. `backend/src/workflow/resume.py` — `coerce_<key>_from_agent_input` so
   `start-from-agent-input` and rerun work.
7. Frontend: usually **nothing** — pipeline cards, ordering, labels, modals,
   download grouping, and CLI all derive from the registry. Only add code if
   the stage needs a bespoke modal (runtime nodes map `kind: "runtime"` →
   modal keyed by agent key in `App.jsx`).

## Renaming an agent key

Grep for the old key in: `schemas.py` maps, `payloads.py`, `engine.py` stage
block, `resume.py`, `prompts/<key>.txt` filename, tests, and any
`agent_outputs/<key>/` expectations. Display-name-only renames are data-only.

## Prompt templates

Prompts may contain `{variable}` placeholders rendered by
`helpers.render_prompt` (plain string replacement — not `str.format` — so
braces in prose/shell/LaTeX need no escaping; unsubstituted placeholders are
logged). Container shell-task prompts: `prompts/bootstrap.txt`
(`{domains_json}`, `{packages}`), `prompts/bootstrapCompile.txt`
(`{tex_path}`, `{basename}`).

## Verify

```bash
.venv/bin/python -m pytest         # backend
.venv/bin/python -m ruff check .   # backend lint
npm -C frontend test               # frontend
npm -C frontend run lint           # frontend lint
```

- Backend tests that swap `AGENTS_CONFIG_PATH` must call
  `registry._load_raw.cache_clear()` first.
- Vitest tests rendering registry-driven components must stub `fetch` for
  `/api/agents` and `await loadAgentRegistry()` before rendering
  (pattern: `frontend/src/components/AgentPipeline.test.jsx`).
- Quick render sanity check without a server:
  `.venv/bin/python -c "from backend.src.registry import agent_sequence, display_names; print(agent_sequence()); print(display_names())"`
