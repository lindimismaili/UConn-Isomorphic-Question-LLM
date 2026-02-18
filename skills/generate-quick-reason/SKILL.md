---
name: "generate-quick-reason"
description: "Generate a one-to-two sentence explanation of why an answer is correct or incorrect."
---

# generate-quick-reason

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- You need a short, student-friendly correctness explanation.

## Workflow
1. State verdict (correct/incorrect).
2. Cite the key mathematical reason in plain language.
3. Keep output to one or two sentences.

## Output
- `{"quick_reason": "..."}`.
