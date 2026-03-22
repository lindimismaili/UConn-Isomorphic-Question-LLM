---
name: "extract-docx"
description: "Extract all text and metadata from a DOCX file into normalized JSON for downstream question workflows."
---

# extract-docx

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- The input file is a `.docx` and you need full text plus authoring metadata.

## Input
- A DOCX path.
- Optional output path.

## Workflow
1. Validate the file exists and has a `.docx` extension.
2. Run `scripts/extract_docx.py` (uses `python-docx`).
3. Read core properties for metadata (`title`, `author`, `created`, `modified`, `subject`).
4. Flatten readable text into `text` while preserving block-level ordering.
5. Add block type tags (`paragraph`, `table`, `header`, `footer`).
6. Write JSON using `../_shared/references/contracts.md`.

## Remote command
```bash
# Run inside the OpenAI agent environment/container only
python skills/extract-docx/scripts/extract_docx.py path/to/file.docx --pretty
```

## Output
- `extracted_document.json` with `source.type = "docx"`.
