---
name: "create-function-for-question"
description: "Create a question-specific Python function that computes the answer from extracted variables."
---

# create-function-for-question

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- `extract-variables` has produced structured inputs and a deterministic solver is needed.

## Workflow
1. Build `solve_question(variables)` as a pure function.
2. Keep logic explicit and deterministic.
3. Include brief inline assertions for expected behavior.
4. Return function code and expected output format.

## Output
- `{"function_code": "def solve_question(variables): ...", "return_schema": {}}`.
