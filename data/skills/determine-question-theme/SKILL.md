---
name: "determine-question-theme"
description: "Classify the question's theme, format, and layout style."
---

# determine-question-theme

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- You need style/theme tags for retrieval, balancing sets, or generation.

## Workflow
1. Identify presentation style (word problem, symbolic, diagram-driven, table-based, proof-style).
2. Identify pedagogical theme (application, procedural fluency, conceptual reasoning).
3. Return 1-3 primary tags with confidence.

## Output
- `{"theme": [{"tag": "...", "confidence": 0.0, "evidence": "..."}]}`.
