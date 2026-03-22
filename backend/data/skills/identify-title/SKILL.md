---
name: "identify-title"
description: "Identify the best document title from extracted metadata and text."
---

# identify-title

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- Title is missing, unreliable, or needs normalization.

## Workflow
1. Prefer explicit metadata title when non-empty.
2. Otherwise infer from top heading-like lines in early blocks.
3. Remove noisy prefixes (`worksheet`, `page 1`, timestamps) unless semantically needed.
4. Return title plus confidence and supporting evidence snippets.

## Output
- `{"title": "...", "confidence": 0.0, "evidence": []}`.
