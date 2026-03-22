---
name: "extract-image"
description: "Extract OCR text and image metadata from an image file into normalized JSON for downstream question workflows."
---

# extract-image

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run Python or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- The input is an image (`.png`, `.jpg`, `.jpeg`, `.webp`, `.tif`) containing question text.

## Input
- An image path.
- Optional output path.

## Workflow
1. Validate extension and existence.
2. Run `scripts/extract_image.py` (uses Pillow and `pytesseract` OCR).
3. Read image metadata (dimensions, mode, DPI, EXIF if present).
4. Normalize OCR output into `blocks` with `kind = "ocr"`.
5. Store unreadable regions or OCR warnings in `metadata.extra.ocr_warnings`.
6. Write JSON using `../_shared/references/contracts.md`.

## Remote command
```bash
# Run inside the OpenAI agent environment/container only
python skills/extract-image/scripts/extract_image.py path/to/file.png --pretty
```

## Output
- `extracted_document.json` with `source.type = "image"`.
