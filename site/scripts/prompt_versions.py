#!/usr/bin/env python3
"""Manage versioned commits for backend prompt templates."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROMPT_DIR = Path("site/backend/data/prompts")
PROMPT_SUFFIX = ".txt"
DEFAULT_LOG_LIMIT = 20


def run_git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=check,
        text=True,
        capture_output=True,
    )


def repo_root() -> Path:
    result = run_git(["rev-parse", "--show-toplevel"], Path.cwd())
    return Path(result.stdout.strip())


def prompt_dir(root: Path) -> Path:
    return root / PROMPT_DIR


def status_lines(root: Path) -> list[str]:
    result = run_git(["status", "--porcelain", "--", str(PROMPT_DIR)], root)
    return [line for line in result.stdout.splitlines() if line.strip()]


def changed_prompt_files(root: Path) -> list[str]:
    files: list[str] = []
    for line in status_lines(root):
        path = line[3:]
        if path.startswith('"') and path.endswith('"'):
            path = path[1:-1]
        files.append(path)
    return sorted(files)


def ensure_prompt_dir(root: Path) -> None:
    directory = prompt_dir(root)
    if not directory.is_dir():
        raise SystemExit(f"Prompt directory not found: {directory}")


def ensure_clean_prompt_worktree(root: Path, force: bool) -> None:
    if force or not status_lines(root):
        return

    raise SystemExit(
        "Prompt files already have uncommitted changes. Run "
        "`python scripts/prompt_versions.py snapshot` first, or rerun restore with --force."
    )


def reject_unmerged_prompts(root: Path) -> None:
    unmerged = [line for line in status_lines(root) if "U" in line[:2]]
    if unmerged:
        raise SystemExit("Prompt files have unresolved merge conflicts; resolve them before snapshotting.")


def resolve_prompt_path(prompt_name: str) -> Path:
    path = Path(prompt_name)
    if path.parts[: len(PROMPT_DIR.parts)] == PROMPT_DIR.parts:
        relative = path
    else:
        name = path.name
        if not name.endswith(PROMPT_SUFFIX):
            name = f"{name}{PROMPT_SUFFIX}"
        relative = PROMPT_DIR / name

    if relative.suffix != PROMPT_SUFFIX or relative.parent != PROMPT_DIR:
        raise SystemExit(f"Prompt must be a {PROMPT_SUFFIX} file directly under {PROMPT_DIR}: {prompt_name}")

    return relative


def prompt_paths_from_args(prompt_names: list[str], root: Path) -> list[Path]:
    if prompt_names:
        return [resolve_prompt_path(name) for name in prompt_names]

    return sorted(path.relative_to(root) for path in prompt_dir(root).glob(f"*{PROMPT_SUFFIX}"))


def commit_message(changed_files: list[str], requested_message: str | None) -> tuple[str, str]:
    if requested_message:
        subject = requested_message
    elif len(changed_files) == 1:
        subject = f"Snapshot prompt version: {Path(changed_files[0]).name}"
    else:
        subject = "Snapshot prompt versions"

    body = "Changed prompt files:\n" + "\n".join(f"- {path}" for path in changed_files)
    return subject, body


def snapshot(args: argparse.Namespace) -> int:
    root = repo_root()
    ensure_prompt_dir(root)
    reject_unmerged_prompts(root)

    changed_files = changed_prompt_files(root)
    if not changed_files:
        print("No prompt changes to snapshot.")
        return 0

    run_git(["add", "--", str(PROMPT_DIR)], root)
    staged_result = run_git(["diff", "--cached", "--name-only", "--", str(PROMPT_DIR)], root)
    staged_files = sorted(line for line in staged_result.stdout.splitlines() if line.strip())
    if not staged_files:
        print("No prompt changes to snapshot.")
        return 0

    subject, body = commit_message(staged_files, args.message)
    run_git(["commit", "-m", subject, "-m", body, "--", str(PROMPT_DIR)], root)

    commit_hash = run_git(["rev-parse", "--short", "HEAD"], root).stdout.strip()
    print(f"Created prompt snapshot commit {commit_hash}.")
    for path in staged_files:
        print(f"  {path}")
    return 0


def list_versions(args: argparse.Namespace) -> int:
    root = repo_root()
    ensure_prompt_dir(root)

    result = run_git(
        [
            "log",
            f"-{args.limit}",
            "--date=short",
            "--pretty=format:%h%x09%ad%x09%s",
            "--",
            str(PROMPT_DIR),
        ],
        root,
    )
    if result.stdout:
        print(result.stdout)
    else:
        print("No prompt version commits found.")
    return 0


def show_version(args: argparse.Namespace) -> int:
    root = repo_root()
    prompt_path = resolve_prompt_path(args.prompt)
    result = run_git(["show", f"{args.commit}:{prompt_path.as_posix()}"], root)
    print(result.stdout, end="")
    return 0


def restore_version(args: argparse.Namespace) -> int:
    root = repo_root()
    ensure_prompt_dir(root)
    ensure_clean_prompt_worktree(root, args.force)

    prompt_paths = prompt_paths_from_args(args.prompts, root)
    run_git(["restore", "--source", args.commit, "--", *[path.as_posix() for path in prompt_paths]], root)

    changed_files = changed_prompt_files(root)
    if not changed_files:
        print("Restore made no prompt changes.")
        return 0

    if args.no_commit:
        print("Restored prompt files without committing:")
        for path in changed_files:
            print(f"  {path}")
        return 0

    message = args.message or f"Restore prompt versions from {args.commit}"
    snapshot_args = argparse.Namespace(message=message)
    return snapshot(snapshot_args)


def print_status(_args: argparse.Namespace) -> int:
    root = repo_root()
    ensure_prompt_dir(root)

    lines = status_lines(root)
    if lines:
        print("\n".join(lines))
    else:
        print("Prompt files are clean.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and inspect prompt-only Git commits for site/backend/data/prompts.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot_parser = subparsers.add_parser("snapshot", help="commit current prompt changes only")
    snapshot_parser.add_argument("-m", "--message", help="commit subject for the prompt snapshot")
    snapshot_parser.set_defaults(func=snapshot)

    list_parser = subparsers.add_parser("list", help="list commits that changed prompt files")
    list_parser.add_argument("-n", "--limit", type=int, default=DEFAULT_LOG_LIMIT)
    list_parser.set_defaults(func=list_versions)

    show_parser = subparsers.add_parser("show", help="print one prompt file from a historical commit")
    show_parser.add_argument("commit")
    show_parser.add_argument("prompt", help="prompt name, e.g. generator or generator.txt")
    show_parser.set_defaults(func=show_version)

    restore_parser = subparsers.add_parser("restore", help="restore prompts from a historical commit")
    restore_parser.add_argument("commit")
    restore_parser.add_argument("prompts", nargs="*", help="prompt names to restore; defaults to all prompts")
    restore_parser.add_argument("--force", action="store_true", help="overwrite uncommitted prompt changes")
    restore_parser.add_argument("--no-commit", action="store_true", help="restore without creating a snapshot commit")
    restore_parser.add_argument("-m", "--message", help="commit subject for the restore snapshot")
    restore_parser.set_defaults(func=restore_version)

    status_parser = subparsers.add_parser("status", help="show prompt-file working tree status")
    status_parser.set_defaults(func=print_status)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
