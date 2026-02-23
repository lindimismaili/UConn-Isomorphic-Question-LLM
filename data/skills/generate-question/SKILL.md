---
name: "generate-question"
description: "Generate a new question from a source question with optional preferences for difficulty/theme/variation."
---

# generate-question

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- You need an isomorphic or controlled-variation question.

## Input
- Source question object.
- Optional preferences: difficulty target, theme variation, numeric variation, choice count.

## Workflow
1. Preserve the target concept while changing surface form as requested.
2. Ensure the generated question is solvable and unambiguous.
3. Provide answer and minimal rationale for internal verification.
4. Avoid copying original wording unless explicitly requested.

## Output
- `generation_output.json` with `stem`, `choices`, `correct_answer`, `difficulty`, `theme`, `concepts`.
