---
name: check-agents-registry
description: Validate the 10-node agents registry, required metadata, ordering, filtered sets, prompt/schema coverage, and Python accessors. Use after changing site/backend/data/agents.json, registry.py, schemas, or prompts.
---

```toml
id = "a69dee74a780ee27d5f56be8b1be382f"
```

# Check agents registry consistency

Run from `site/` with the project environment. Do not fall back to a shared or
home-directory environment.

```bash
test -x .venv/bin/python
```

## Inspect the registry

```bash
.venv/bin/python - <<'PY'
from backend.src.registry import (
    agent_sequence,
    aliases_map,
    all_agents,
    guardrail_nodes,
    input_to_agent_sequence,
    llm_agents,
    promptable_agents,
    guardrail_max_output_tokens,
    setup_max_output_tokens,
    stoppable_agents,
)
from backend.src.paths import PROMPTS_DIR
from backend.src.schemas import AGENT_INPUT_TYPES, AGENT_OUTPUT_TYPES

agents = all_agents()
seq = agent_sequence()
required = {
    "sequence", "name", "description", "model", "reasoning", "kind", "isLLM",
    "hasPrompt", "acceptsSourceFiles", "isStoppable", "useCodeInterpreter",
    "countLabel", "aliases", "inputFrom", "outputTo", "cliDescription",
}

assert len(seq) == 10, seq
assert len(seq) == len(set(seq))
assert set(seq) == set(agents)
assert list(seq) == sorted(seq, key=lambda key: float(agents[key]["sequence"]))

for key, entry in agents.items():
    missing = sorted(required - set(entry))
    assert not missing, f"{key}: missing {missing}"
    assert entry["kind"] in {"runtime", "guardrail", "agent"}, (key, entry["kind"])
    assert not [alias for alias in entry["aliases"] if alias], (key, entry["aliases"])
    if entry["isStoppable"]:
        assert entry.get("resultField"), f"{key}: stoppable node needs resultField"
    if entry["countLabel"] is not None:
        assert entry.get("countField"), f"{key}: counted node needs countField"
    if key in {"normalizer", "extracter", "verifier", "generator", "latexifier"}:
        value = entry.get("maxOutputTokens")
        assert isinstance(value, int) and not isinstance(value, bool), (key, value)
        assert 256 <= value <= 131_072, (key, value)

assert 256 <= setup_max_output_tokens() <= 131_072
assert 256 <= guardrail_max_output_tokens() <= 131_072

guardrails = set(guardrail_nodes())
assert guardrails
assert guardrails.isdisjoint(input_to_agent_sequence())
assert guardrails.isdisjoint(llm_agents())
assert guardrails.isdisjoint(stoppable_agents())
assert aliases_map() == {}

llm_set = set(llm_agents())
assert set(AGENT_INPUT_TYPES) == llm_set
assert set(AGENT_OUTPUT_TYPES) == llm_set - {"evaluation"}
assert agents["evaluation"].get("promptName") == "evaluator"
for prompt_name in promptable_agents():
    assert (PROMPTS_DIR / f"{prompt_name}.txt").is_file(), prompt_name

print("Sequence:", " -> ".join(seq))
print("LLM agents:", llm_agents())
print("Stoppable:", stoppable_agents())
print("Guardrails:", guardrail_nodes())
PY
```

`evaluation` is intentionally special: it is an LLM registry node with an
`EvaluationInput`, but grading is executed by the OpenAI Evals integration
rather than the normal Agents SDK factory. It therefore belongs in
`AGENT_INPUT_TYPES`, not `AGENT_OUTPUT_TYPES`, and its prompt file is selected
through `promptName: "evaluator"`.

Do not introduce a static model allowlist. If the task intentionally changes a
model policy, validate the requested values and all consumers rather than
rejecting any model not named in this skill.

## Verify

```bash
OPENAI_API_KEY= APP_ACCESS_TOKEN= .venv/bin/python -m pytest backend/tests
.venv/bin/python -m ruff check .
npm -C frontend test
npm -C frontend run lint -- --max-warnings=0
```

Report the ten-node sequence, filtered sets, metadata/schema/prompt gaps, and
the exact verification results. Restart the backend and reload the page before
manual verification because both registry readers cache data.
