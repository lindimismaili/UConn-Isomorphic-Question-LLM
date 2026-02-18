---
name: "check-for-answers"
description: "Detect whether explicit answers or answer keys are present for each question."
---

# check-for-answers

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- Questions have been identified and you want to detect explicit answer evidence before reasoning.

## Workflow
1. Search for answer-key sections, inline `Answer:` hints, and final marked choices.
2. Match each explicit answer to a `question_id` by proximity and numbering.
3. Mark cases as `explicit`, `ambiguous`, or `not_found`.
4. Return evidence spans exactly as they appear in source text.

## Output
- `answer_analysis.json` entries with `explicit_answer_found` and `explicit_evidence`.
