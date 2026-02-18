---
name: "evaluate-via-python"
description: "Evaluate questions with deterministic Python code by chaining variable extraction and function construction."
---

# evaluate-via-python

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- Symbolic or numeric verification should be done programmatically.

## Workflow
1. Run `extract-variables` to create a normalized variable payload.
2. Run `create-function-for-question` to produce a solver function.
3. Execute solver in Python and capture output plus intermediate trace.
4. Compare against answer choices/candidate answer.
5. Return a machine-checkable verdict.

## Output
- `{"python_evaluation": {"result": "...", "matches_choice": "...", "trace": []}}`.
