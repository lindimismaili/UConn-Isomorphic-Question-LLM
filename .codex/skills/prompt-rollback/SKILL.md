---
name: prompt-rollback
description: List recent committed versions of UConn Isomorphic Question Generator prompt templates, ask the user which version to restore, then restore selected `backend/data/prompts/*.txt` files and commit the rollback as a prompt-only snapshot. Use when the user asks to roll back, revert, restore, switch back, compare, or see previous prompt versions.
---

# Prompt Rollback

## Workflow

1. Work from the repository root:

   ```bash
   cd /Users/lindimismaili/GitHub/UConn-IsomorphicQuestionLLM
   ```

2. Read `AGENTS.md`, then check prompt and repository state:

   ```bash
   python scripts/prompt_versions.py status
   git status --short
   ```

   If prompt files have uncommitted changes, do not overwrite them. Ask whether to snapshot those edits first or abandon them before rollback.

3. Show the user recent prompt versions. Default to the last 10 unless the user asked for a different count:

   ```bash
   python scripts/prompt_versions.py list -n 10
   ```

   If the user asks to see more, rerun with the requested `-n` value.

4. Ask which commit they want to roll back to and whether to restore all prompts or specific prompt files. Keep the question concise and include the commit hashes from the list.

5. If the user wants to inspect a candidate first, show relevant prompt content:

   ```bash
   python scripts/prompt_versions.py show <commit> <prompt>
   ```

6. Restore and commit the selected version:

   ```bash
   python scripts/prompt_versions.py restore <commit> [prompt...]
   ```

   The restore command creates a new prompt-only commit by default. Use `--no-commit` only if the user explicitly asks to inspect restored files before committing.

7. Report the restored commit source, restored prompt file(s), new rollback commit hash, and final prompt status.

## Selection Rules

- If the user names a prompt like `generator`, pass `generator` to the helper; it resolves to `backend/data/prompts/generator.txt`.
- If the user says "all prompts", omit prompt names from `restore`.
- If multiple commits look plausible, ask before restoring. Do not guess.
- Never use destructive Git commands such as `git reset --hard` or `git checkout --` for this workflow.
