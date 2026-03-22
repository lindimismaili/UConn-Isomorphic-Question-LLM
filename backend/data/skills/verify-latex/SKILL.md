---
name: "verify-latex"
description: "Verify generated LaTeX for syntax/render issues and auto-fix common problems."
---

# verify-latex

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- A `.tex` file was generated and must be checked before delivery.

## Workflow
1. Lint quickly for unbalanced braces, math mode issues, and special-character escapes.
2. If linting finds issues, patch common problems and re-check once.
3. Return final status and fixes applied.

## Output
- `{"latex_valid": true|false, "issues": [], "fixes_applied": []}`.
