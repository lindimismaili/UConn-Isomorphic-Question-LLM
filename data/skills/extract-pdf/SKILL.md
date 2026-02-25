---
name: "extract-pdf"
description: "Extract all text and metadata from a PDF into normalized JSON for downstream question workflows."
---

# extract-pdf

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- The input file is a `.pdf` and must be converted into machine-usable text plus metadata.

## Input
- A PDF path.
- Optional output path.

## Workflow
1. Validate the file exists and has a `.pdf` extension.
2. Run `scripts/extract_pdf.py` (uses `pypdf` with `pdfplumber` fallback).
3. Extract document metadata (`title`, `author`, creation/modification fields, page count).
4. Extract text per page and assemble `blocks` with page indexes.
5. If a page is empty, mark it as OCR-needed in `metadata.extra.needs_ocr_pages`.
6. Write JSON using the shared contract at `../_shared/references/contracts.md`.

## Remote command
```bash
# Run inside the OpenAI agent environment/container only
python skills/extract-pdf/scripts/extract_pdf.py path/to/file.pdf --pretty
```

## Output
- `extracted_document.json` with `source.type = "pdf"`.
