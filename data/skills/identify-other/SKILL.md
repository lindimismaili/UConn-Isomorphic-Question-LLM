---
name: "identify-other"
description: "Extract additional useful metadata not covered by title/date/author."
---

# identify-other

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- You need richer context fields for downstream analysis.

## Workflow
1. Extract course, grade level, topic tags, language, institution, exam type, and version markers.
2. Parse difficulty hints (`honors`, `AP`, `intro`) without treating them as final difficulty labels.
3. Preserve raw evidence strings for auditability.

## Output
- `{"other": {"course": null, "grade_level": null, "language": null, "tags": [], "evidence": []}}`.
