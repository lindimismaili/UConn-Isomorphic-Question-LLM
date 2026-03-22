---
name: "generate-distractors"
description: "Generate realistic multiple-choice distractors based on common mistakes."
---

# generate-distractors

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- You need plausible wrong answer choices for MCQ construction.

## Workflow
1. Derive distractors from known misconception patterns (sign errors, operation order, unit conversion mistakes).
2. Keep distractors mutually distinct and close in magnitude/form to the correct answer.
3. Validate each distractor is actually incorrect.
4. Return misconception label for each distractor.

## Output
- `{"distractors": [{"text": "...", "mistake_type": "..."}]}`.
