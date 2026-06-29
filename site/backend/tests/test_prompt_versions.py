from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "prompt_versions.py"


def run_command(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, check=check, text=True, capture_output=True)


def run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return run_command(["git", *args], cwd)


def run_prompt_versions(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run_command([sys.executable, str(SCRIPT_PATH), *args], cwd, check=check)


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    prompts = repo / "backend" / "data" / "prompts"
    prompts.mkdir(parents=True)
    shutil.copyfile(REPO_ROOT / "backend" / "data" / "prompts" / "generator.txt", prompts / "generator.txt")
    shutil.copyfile(REPO_ROOT / "backend" / "data" / "prompts" / "verifier.txt", prompts / "verifier.txt")

    run_git(["init"], repo)
    run_git(["config", "user.email", "test@example.com"], repo)
    run_git(["config", "user.name", "Prompt Test"], repo)
    run_git(["add", "backend/data/prompts"], repo)
    run_git(["commit", "-m", "Initial prompts"], repo)
    return repo


def test_snapshot_commits_only_prompt_changes(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    readme = repo / "README.md"
    readme.write_text("unrelated\n")
    run_git(["add", "README.md"], repo)

    generator = repo / "backend" / "data" / "prompts" / "generator.txt"
    generator.write_text(generator.read_text() + "\nTest prompt addition.\n")

    result = run_prompt_versions(["snapshot", "-m", "Test prompt snapshot"], repo)

    assert "Created prompt snapshot commit" in result.stdout
    commit_files = run_git(["show", "--name-only", "--pretty=format:", "HEAD"], repo).stdout.splitlines()
    assert commit_files == ["backend/data/prompts/generator.txt"]
    staged_files = run_git(["diff", "--cached", "--name-only"], repo).stdout.splitlines()
    assert staged_files == ["README.md"]


def test_restore_commits_prompt_from_previous_version(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    original_commit = run_git(["rev-parse", "--short", "HEAD"], repo).stdout.strip()

    generator = repo / "backend" / "data" / "prompts" / "generator.txt"
    original_text = generator.read_text()
    generator.write_text(original_text + "\nExperimental prompt line.\n")
    run_prompt_versions(["snapshot", "-m", "Experimental generator prompt"], repo)

    result = run_prompt_versions(["restore", original_commit, "generator"], repo)

    assert "Created prompt snapshot commit" in result.stdout
    assert generator.read_text() == original_text
    subject = run_git(["log", "-1", "--pretty=%s"], repo).stdout.strip()
    assert subject == f"Restore prompt versions from {original_commit}"


def test_show_prints_prompt_from_commit(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    commit = run_git(["rev-parse", "--short", "HEAD"], repo).stdout.strip()
    expected_text = (repo / "backend" / "data" / "prompts" / "verifier.txt").read_text()

    result = run_prompt_versions(["show", commit, "verifier"], repo)

    assert result.stdout == expected_text
