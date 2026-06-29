# Gitignore Cleanup Design

## Goal

Make the repository `.gitignore` easier to scan and maintain while preserving
its current behavior, adapting runtime paths to the `site/` layout, and ignoring
the repository-local `.remember/` workspace.

## Design

- Group patterns by purpose: Python, JavaScript, generated output, developer
  tools, operating-system files, local configuration, and runtime artifacts.
- Replace overlapping Node.js rules with one recursive `node_modules/` rule and
  one generic `.vite/` rule.
- Add `.remember/` to the developer-tool section so the directory and all of its
  contents remain local.
- Preserve the tracked `.gitkeep` exceptions under `site/backend/uploads/` and
  `site/backend/outputs/`.
- Do not modify or stage unrelated worktree changes.

## Verification

- Use `git check-ignore -v` to confirm `.remember/` and representative generated
  files are ignored.
- Use `git check-ignore -v` to confirm the tracked `.gitkeep` files remain
  unignored.
- Review the final `.gitignore` diff for scope and duplicate rules.
