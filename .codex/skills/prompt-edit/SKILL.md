---
name: prompt-edit
description: Edit UConn Isomorphic Question Generator prompt templates and immediately commit the prompt changes as a prompt-only version snapshot. Use when the user asks to change, tune, test, revise, improve, or experiment with files under `backend/data/prompts/*.txt`, especially in `/Users/lindimismaili/GitHub/UConn-IsomorphicQuestionLLM`.
---

# Prompt Edit

## Workflow

1. Work from the repository root:

   ```bash
   cd /Users/lindimismaili/GitHub/UConn-IsomorphicQuestionLLM
   ```

2. Read `AGENTS.md` and the relevant prompt file(s) under `backend/data/prompts/`.

3. Before editing, run:

   ```bash
   python scripts/prompt_versions.py status
   git status --short
   ```

   If prompt files already have uncommitted changes, inspect them and include them only when they match the user's request. Do not overwrite unrelated user work.

4. Edit only the prompt file(s) needed for the requested behavior. Prompt files are hand-curated; do not rename, delete, broadly rewrite, or reformat them unless explicitly asked.

5. Review the diff:

   ```bash
   git diff -- backend/data/prompts
   ```

6. Create a concise commit message that names the user-visible prompt behavior, then snapshot the prompt changes immediately:

   ```bash
   python scripts/prompt_versions.py snapshot -m "<concise prompt-change summary>"
   ```

   Use this helper rather than raw `git commit`; it stages only `backend/data/prompts` and preserves unrelated staged or unstaged work.

7. Report the prompt file(s) changed, the commit hash created by the snapshot command, and any verification performed.

## Commit Message Guidance

Use short, specific commit subjects:

- `Tune generator distractor logic prompt`
- `Clarify verifier answer validation prompt`
- `Tighten latexifier formatting instructions`

Avoid vague subjects such as `prompt changes` or `update prompts`.

## Validation

Prompt-only edits usually do not require a full app test. If the user asks for a real workflow run, use the repo's normal CLI or UI workflow after the snapshot commit has been created.
