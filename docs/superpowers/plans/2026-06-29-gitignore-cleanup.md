# Gitignore Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Organize the repository ignore rules, remove redundant patterns, and keep `.remember/` local.

**Architecture:** This is a single-file configuration cleanup. Existing ignore behavior remains intact except for generalized recursive cache rules and the new `.remember/` exclusion.

**Tech Stack:** Git ignore patterns and Git verification commands.

---

### Task 1: Organize and verify ignore rules

**Files:**
- Modify: `.gitignore`

- [x] **Step 1: Update the ignore rules**

Group related patterns, replace redundant Node.js and Vite paths with recursive
rules, add `.remember/`, and retain the `site/backend` `.gitkeep` exceptions.

- [x] **Step 2: Check formatting and the final diff**

Run: `git diff --check -- .gitignore && git diff -- .gitignore`

Expected: no whitespace errors; the diff contains only the approved cleanup.

- [x] **Step 3: Verify ignored paths**

Run:

```bash
git check-ignore -v .remember/now.md site/frontend/node_modules/example site/frontend/.vite/example site/backend/runtime/example.sqlite3
```

Expected: every path is matched by its intended rule.

- [x] **Step 4: Verify tracked placeholders**

Run:

```bash
git check-ignore site/backend/uploads/.gitkeep site/backend/outputs/.gitkeep
```

Expected: exit status 1 with no output, confirming both files remain unignored.

- [ ] **Step 5: Commit only this task's files**

Run:

```bash
git commit --only .gitignore docs/superpowers/specs/2026-06-29-gitignore-cleanup-design.md docs/superpowers/plans/2026-06-29-gitignore-cleanup.md -m "Organize repository ignore rules"
```

Expected: one commit containing only the `.gitignore` cleanup and its approved workflow documents; unrelated staged changes remain staged.
