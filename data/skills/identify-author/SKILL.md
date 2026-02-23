---
name: "identify-author"
description: "Identify the likely author from metadata and document cues."
---

# identify-author

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- Author information is required for provenance, indexing, or citation.

## Workflow
1. Use metadata author fields first.
2. Parse bylines/signatures (`Prepared by`, `Author`, `Instructor`) in text.
3. De-duplicate aliases and normalize name capitalization.
4. Return primary author and alternative candidates with confidence.

## Output
- `{"author": "...|null", "alternatives": [], "confidence": 0.0}`.
