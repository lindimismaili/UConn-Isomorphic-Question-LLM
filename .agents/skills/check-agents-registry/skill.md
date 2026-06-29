---
name: check-agents-registry
description: Verify that backend/data/agents.json is internally consistent and that registry.py returns the expected data after any edit. Use after modifying agents.json, registry.py, or helpers.py. Checks required fields, model consistency, no non-empty aliases, registry accessor outputs, and that all backend tests still pass.
---

# Check agents.json + registry consistency

Run this after any edit to `backend/data/agents.json` or `backend/src/registry.py`.
It catches missing fields, stale defaults, and alias drift before they hit tests.

## Step 1 — Activate Python environment

```bash
source .venv/bin/activate 2>/dev/null || source ~/venvs/main_py3*/bin/activate 2>/dev/null || true
```

## Step 2 — Spot-check registry accessors

```bash
python -c "
from backend.src.registry import (
    agent_sequence, all_agents, llm_agents, stoppable_agents,
    source_file_agents, aliases_map, display_names,
    setup_bootstrap_model, setup_allowed_domains, setup_container_packages,
    evaluation_model, api_title, themes,
)
agents = all_agents()
seq = agent_sequence()

print('=== Sequence ===')
for i, k in enumerate(seq):
    e = agents[k]
    print(f'  [{i}] {k:12}  model={e[\"model\"]:8}  kind={e[\"kind\"]:8}  isLLM={e.get(\"isLLM\")}')

print()
print('=== Filtered sets ===')
print('  LLM agents:      ', llm_agents())
print('  Stoppable:       ', stoppable_agents())
print('  Source-file:     ', source_file_agents())
print('  aliases_map:     ', aliases_map())

print()
print('=== Setup config ===')
print('  bootstrap model: ', setup_bootstrap_model())
print('  allowed domains: ', setup_allowed_domains())
print('  python packages: ', setup_container_packages())

print()
print('=== Eval + API ===')
print('  evaluation model:', evaluation_model())
print('  api title:       ', api_title())
print('  themes:          ', [t[\"label\"] for t in themes()])

print()
print('=== Field completeness ===')
required = ['sequence','model','reasoning','kind','isLLM','hasPrompt','isStoppable',
            'acceptsSourceFiles','useCodeInterpreter','countLabel','aliases','cliDescription']
for key, entry in agents.items():
    missing = [f for f in required if f not in entry]
    if missing:
        print(f'  MISSING in {key}: {missing}')
    non_empty_aliases = [a for a in entry.get('aliases', []) if a]
    if non_empty_aliases:
        print(f'  NON-EMPTY aliases in {key}: {non_empty_aliases}')
    if entry.get('model') != 'gpt-5.5':
        print(f'  UNEXPECTED model in {key}: {entry.get(\"model\")}')
print('  Field check complete.')
"
```

Expect: sequence has 8 entries, all models `gpt-5.5`, `aliases_map()` returns `{}`,
LLM agents includes evaluation, stoppable excludes evaluation/setup/output.

## Step 3 — Run backend tests

```bash
python -m pytest backend/tests/ -q
```

All tests must pass. If `test_workflow_and_grading_models_use_gpt_5_5` fails,
a non-LLM or null-model agent crept into the LLM filter — check `isLLM` fields.

## Step 4 — Run ruff lint

```bash
python -m ruff check backend/src/
```

## Step 5 — Report

Summarize:
- Registry sequence (all 8 in order)
- Any missing fields or non-empty aliases found
- Test result (pass count)
- Ruff result (clean or violations)
