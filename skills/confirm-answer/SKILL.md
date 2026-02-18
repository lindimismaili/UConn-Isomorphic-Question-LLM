---
name: "confirm-answer"
description: "Reason through each question to confirm the correct answer when explicit keys are absent or uncertain."
---

# confirm-answer

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- `check-for-answers` returned `not_found` or `ambiguous`.

## Workflow
1. Solve the question independently from first principles.
2. Evaluate each choice, eliminating invalid options with explicit reasons.
3. If all choices are wrong, return `none_of_the_above` and provide the computed correct answer.
4. Record confidence and validation checks.

## Output
- `answer_analysis.json` entries with `reasoned_answer`, `confidence`, and `checks`.
