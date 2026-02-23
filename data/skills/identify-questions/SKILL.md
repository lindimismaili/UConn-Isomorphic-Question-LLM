---
name: "identify-questions"
description: "Identify question boundaries, stems, and answer choices from extracted text and metadata."
---

# identify-questions

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- You already have `extracted_document.json` and need structured question objects.

## Workflow
1. Detect question boundaries using numbering, punctuation, and layout cues.
2. Parse answer choices from patterns like `A)`, `A.`, `(A)`, `1)`, etc.
3. Preserve source traceability with `source_block_ids`.
4. Return one normalized `questions[]` list.
5. If parsing is ambiguous, include an ambiguity note per question.

## Output
- `identified_questions.json` following `../_shared/references/contracts.md`.
