---
name: "determine-question-concept"
description: "Identify the mathematical concept set required to solve each question."
---

# determine-question-concept

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- Concept tagging is needed for curriculum alignment or isomorphic generation.

## Workflow
1. Map the question to canonical concepts (e.g., linear equations, ratios, probability, limits).
2. Separate primary concept from supporting concepts.
3. Include short evidence for each concept tag.

## Output
- `{"concepts": {"primary": "...", "secondary": [], "evidence": []}}`.
