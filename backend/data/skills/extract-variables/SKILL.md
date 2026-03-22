---
name: "extract-variables"
description: "Extract numeric and symbolic variables from a question into Python-ready structured data."
---

# extract-variables

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- You need structured values before writing a solver function.

## Workflow
1. Extract numbers, fractions, percentages, units, and symbolic parameters.
2. Normalize values to explicit numeric forms when safe.
3. Preserve original text spans for traceability.
4. Return JSON with typed variables.

## Output
- `{"variables": [{"name": "...", "value": 0, "unit": null, "source_text": "..."}]}`.
