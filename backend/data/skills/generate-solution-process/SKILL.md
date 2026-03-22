---
name: "generate-solution-process"
description: "Generate a clear step-by-step solution process for a question."
---

# generate-solution-process

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- You need transparent instructional steps, not only the final answer.

## Workflow
1. Break the solution into ordered steps.
2. Keep each step mathematically valid and concise.
3. Include intermediate values where they prevent ambiguity.
4. End with a boxed/final statement.

## Output
- `{"solution_process": ["step 1", "step 2", "..."]}`.
