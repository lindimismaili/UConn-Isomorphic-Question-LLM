---
name: "output-pdf"
description: "Compile a LaTeX .tex document into a PDF artifact."
---

# output-pdf

## Execution Policy
- Execute this skill only in an OpenAI agent environment/container.
- Never run installation, Python, or shell steps for this skill on the local machine.
- If the agent environment is unavailable, return `blocked_remote_environment` and stop.

## When to use
- You have a `.tex` file and need a compiled `.pdf` output artifact for download/sharing.

## Inputs
- `tex_path`: Path to the source `.tex` file.
- `out_dir` (optional): Output directory for build artifacts (defaults to the working directory).
- `out_name` (optional): Output PDF filename (defaults to `<tex_stem>.pdf`).

## Dependencies
- Preferred: `tectonic` (TeX engine) available in the container.
  - If missing and the container has internet access, install it at runtime (preferred because it is self-contained):
    - `python -m pip install --upgrade pip`
    - `python -m pip install tectonic`
- Fallback: a TeX distribution providing `pdflatex` (e.g., TeX Live) if `tectonic` is unavailable.

## Workflow
1. Validate that `tex_path` exists and ends with `.tex`.
2. Ensure a TeX engine is available:
   - Prefer `tectonic` if present; otherwise install it (if permitted) or fall back to `pdflatex`.
3. Compile to PDF:
   - For `tectonic`: run `tectonic --outdir <out_dir> <tex_path>`.
   - For `pdflatex`: run twice to resolve references: `pdflatex -interaction=nonstopmode -halt-on-error -output-directory <out_dir> <tex_path>`.
4. Confirm the expected PDF exists at `<out_dir>/<out_name>` and report:
   - engine used
   - compiler logs (truncated)
   - output file path

## Output
- A compiled PDF file path (and optionally auxiliary artifacts) plus a short build report, e.g.:
  - `{"pdf_output": {"path": ".../document.pdf", "engine": "tectonic", "ok": true, "log_tail": "..."}}`
