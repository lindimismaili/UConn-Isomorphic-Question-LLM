# Archive Guide

This directory preserves source material, earlier implementations, and project
reports that are useful for historical reference. It is not part of the live
FastAPI, React, or OpenAI Agents SDK application under `site/`, and application
code should not import files from here.

## Directory Overview

```text
archive/
├── ARCHIVE.md
├── GPT Builder Versions/
│   ├── Version 1/
│   ├── Version 2/
│   ├── Version 3/
│   ├── Version 4/
│   ├── Version 5/
│   └── Version 6/
├── Question Data Files/
│   ├── DOCX/
│   └── PDF/
└── Reports/
```

## GPT Builder Versions

`GPT Builder Versions/` contains six historical versions of the custom GPT
created with the GPT Builder in ChatGPT. This was the project's original
implementation before the workflow moved to Codex and a multi-agent OpenAI
Agents SDK architecture.

These files are snapshots for understanding how the original prompt,
verification rules, structured question format, and LaTeX output evolved. They
are not the prompts used by the current application; the current agent registry
and prompts live under `site/backend/data/`.

### Versions 1-4

Each of these versions contains:

- `System_Prompt.txt`: the complete GPT Builder instructions for generating,
  verifying, and formatting isomorphic undergraduate physics questions.
- `LaTeX_Template.txt`: an example/template for rendering generated
  multiple-choice questions, correct answers, explanations, and distractor
  reasoning as LaTeX.

The successive versions refine the generation pipeline, JSON output,
verification process, formatting requirements, and packaging behavior. Version
4 also develops the non-breaking LaTeX question-block format.

### Versions 5-6

The later versions split the previously combined template material into more
focused reference files:

- `System_Prompt.txt`: the GPT Builder's main role, workflow, restrictions, and
  generation instructions.
- `MCQ_Schema.txt`: the structured JSON shape for a generated multiple-choice
  question.
- `Verification_Schema.txt`: the expected verification result fields.
- `LaTeX_Header_Example.txt`: the document preamble and page header.
- `LaTeX_Question_Schema.txt`: the expected LaTeX structure for each question,
  including answer and distractor explanations.

Version 6 is the final GPT Builder snapshot preserved here before development
moved to the current agent-based application.

## Question Data Files

`Question Data Files/` contains the original PHYS 1401 assessment documents
used as source material for generating and testing isomorphic questions. Files
are separated by format:

- `DOCX/`: editable Microsoft Word versions of Exams 1-3 and Quizzes 1-11.
- `PDF/`: PDF versions of the same exams and quizzes, plus
  `Quiz_Compilation.pdf`, which combines quiz material into one source file.

Exam files ending in `_alt` are alternate forms of the corresponding exam, not
duplicate copies. The PDF and DOCX versions are intentionally both retained:
they represent different file formats for the same assessment and support
testing document ingestion across formats.

## Reports

`Reports/` contains project documentation written as Apple Pages files:

- `GPT_Process.pages`: documentation of the earlier GPT-based process.
- `GenAI_Survey_Oct_2025.pages`: the October 2025 generative-AI survey.
- `Iso_Current_Update_V1.pages`: the first saved version of a project-status
  update.
- `Iso_Current_Update_V2.pages`: the second saved version of that project-status
  update.

Open these files in Apple Pages to view or edit their full contents. Both
project-update versions are retained to preserve their revision history.

## Maintenance Notes

- Keep historical GPT Builder files separate from the current prompts and agent
  definitions.
- Keep question documents in the `DOCX/` or `PDF/` directory matching their
  actual format.
- Preserve alternate exam forms and intentionally retained report revisions.
- Before deleting a suspected duplicate, compare file contents rather than
  relying only on similar filenames.
