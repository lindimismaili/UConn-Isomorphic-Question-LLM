"""Canonical filesystem paths used across the backend.

Every module that needs to find data, prompts, outputs, or runtime state should
import from here instead of recomputing `Path(__file__).resolve().parent` chains.
"""

from __future__ import annotations

from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = THIS_DIR.parent if THIS_DIR.name == "src" else THIS_DIR
REPO_ROOT = BACKEND_ROOT.parent

ENV_PATH = REPO_ROOT / ".env"

DATA_DIR = BACKEND_ROOT / "data"
PROMPTS_DIR = DATA_DIR / "prompts"
EVALUATION_FIXTURES_DIR = DATA_DIR / "evaluation"
TESTING_DIR = DATA_DIR / "testing"

AGENTS_CONFIG_PATH = DATA_DIR / "agents.json"
RUBRIC_PATH = DATA_DIR / "rubric.json"
EVALUATOR_PROMPT_PATH = PROMPTS_DIR / "evaluator.txt"
LATEX_TEMPLATE_PATH = DATA_DIR / "latex_template.tex"

OUTPUTS_DIR = BACKEND_ROOT / "outputs"
UPLOADS_DIR = BACKEND_ROOT / "uploads"
RUNTIME_DIR = BACKEND_ROOT / "runtime"

DIST_DIR = REPO_ROOT / "dist"
VERSION_PATHS = (REPO_ROOT / "version.json", DIST_DIR / "version.json")

QUESTION_DATA_DIR = REPO_ROOT / "Question Data Files"
