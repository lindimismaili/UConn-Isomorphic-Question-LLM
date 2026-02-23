---
name: "verify-answer"
description: "Verify whether a proposed answer is correct with deterministic reasoning and explicit checks."
---

# verify-answer

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- A candidate answer must be confirmed as fully correct.

## Workflow
1. Solve independently.
2. Compare computed result against the proposed answer.
3. Validate units, domain restrictions, and rounding assumptions.
4. Return verdict and corrected answer if needed.

## Output
- `{"is_correct": true|false, "correct_answer": "...", "explanation": "...", "checks": []}`.
