---
name: "identify-date"
description: "Extract and normalize relevant dates from metadata and text."
---

# identify-date

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- A document or question set needs date attribution.

## Workflow
1. Collect candidate dates from metadata (`created_at`, `modified_at`) and text.
2. Parse multiple formats (`MM/DD/YYYY`, `YYYY-MM-DD`, month-name forms).
3. Normalize to ISO-8601 when possible.
4. Return all candidates and a selected `primary_date` with rationale.

## Output
- `{"primary_date": "YYYY-MM-DD|null", "candidates": [], "rationale": "..."}`.
