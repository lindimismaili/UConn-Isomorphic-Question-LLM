---
name: "convert-to-latex"
description: "Convert structured JSON question data into LaTeX using a provided template."
---

# convert-to-latex

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- You need final question artifacts in LaTeX from JSON payloads.

## Input
- JSON payload and a template path.

## Workflow
1. Map JSON fields to template placeholders.
2. Escape LaTeX-sensitive characters in user/content text.
3. Preserve math expressions and choice formatting.
4. Write `.tex` output path from payload.

## Output
- A rendered `.tex` file and a conversion report.
