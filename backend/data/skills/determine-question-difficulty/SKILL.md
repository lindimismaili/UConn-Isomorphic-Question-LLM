---
name: "determine-question-difficulty"
description: "Estimate question difficulty using transparent, math-specific criteria."
---

# determine-question-difficulty

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- You need a difficulty label for analytics or generation constraints.

## Workflow
1. Score complexity from required steps, abstraction level, and error-proneness.
2. Use both categorical and numeric outputs.
3. Keep rationale short and evidence-based.

## Output
- `{"difficulty": {"label": "very_easy|easy|medium|hard|olympiad", "score": 1-10, "rationale": "..."}}`.
