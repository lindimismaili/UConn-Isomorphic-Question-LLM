"""Regression checks for the professor-facing environment configuration contract."""

from __future__ import annotations

import re
from pathlib import Path

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"
ROOT_GITIGNORE = Path(__file__).resolve().parents[3] / ".gitignore"

EXPECTED_RUNTIME_KEYS = {
    "ACTIVE_WORKFLOW_STALE_SECONDS",
    "ALLOWED_ORIGINS",
    "APP_ACCESS_TOKEN",
    "APP_RATE_LIMIT_REQUESTS",
    "APP_RATE_LIMIT_WINDOW_SECONDS",
    "ARTIFACT_CLEANUP_INTERVAL_SECONDS",
    "BATCH_WORKFLOW_CONCURRENCY",
    "DOWNLOAD_ZIP_MAX_SOURCE_BYTES",
    "ENABLE_STAGE_CACHE",
    "GENERATION_STRATEGY",
    "GENERATOR_STYLE_REVIEW_ENABLED",
    "GRADER_MAX_COMPLETION_TOKENS",
    "GRADER_MODEL_OVERRIDE",
    "GUARDRAILS_ENABLED",
    "HOST",
    "MAX_ACTIVE_WORKFLOWS",
    "MAX_ACTIVE_BATCH_JOBS",
    "MAX_CREATE_DATA_ENTRIES",
    "MAX_EVAL_BATCH_ENTRIES",
    "MAX_EVAL_BATCH_ITEMS",
    "MAX_EVALUATION_ITEMS",
    "MAX_GENERATED_QUESTIONS",
    "MAX_GENERATED_ARTIFACT_BYTES",
    "MAX_SOURCE_QUESTIONS",
    "MAX_UPLOAD_BYTES",
    "MAX_UPLOAD_TOTAL_BYTES",
    "OPENAI_AGENTS_DISABLE_TRACING",
    "OPENAI_AGENT_STAGE_TIMEOUT_SECONDS",
    "OPENAI_API_KEY",
    "OPENAI_CONTAINER_ALLOWED_DOMAINS",
    "OPENAI_CONTAINER_EXPIRES_MINUTES",
    "OPENAI_CONTAINER_TASK_TIMEOUT_SECONDS",
    "OPENAI_EVALUATION_TIMEOUT_SECONDS",
    "OPENAI_FILE_EXPIRES_SECONDS",
    "OPENAI_GUARDRAIL_MAX_CLASSIFIER_REQUESTS",
    "OPENAI_GUARDRAIL_STAGE_TIMEOUT_SECONDS",
    "OPENAI_GUARDRAIL_TIMEOUT_SECONDS",
    "OPENAI_PROVIDER_MAX_RETRIES",
    "OPENAI_PROVIDER_REQUEST_TIMEOUT_SECONDS",
    "OPENAI_STATUS_CACHE_SECONDS",
    "OPENAI_STATUS_TIMEOUT_SECONDS",
    "OPENAI_WORKFLOW_BLOCKING_CONCURRENCY",
    "OPENAI_WORKFLOW_CONCURRENCY",
    "OUTPUT_MAX_BYTES",
    "OUTPUT_MAX_RUNS",
    "OUTPUT_RETENTION_SECONDS",
    "PAYPAL_DONATE_URL",
    "PORT",
    "PRUNE_INTERVAL_SECONDS",
    "STAGE_CACHE_MAX_ENTRIES",
    "STAGE_CACHE_MAX_RECORD_BYTES",
    "STAGE_CACHE_TTL_SECONDS",
    "UPLOAD_MAX_BYTES",
    "UPLOAD_RETENTION_SECONDS",
    "WORKFLOW_MAX_ENTRIES",
    "WORKFLOW_TOKEN_STREAMING_ENABLED",
    "WORKFLOW_TTL_SECONDS",
}


def _documented_keys(text: str) -> set[str]:
    pattern = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)=", re.MULTILINE)
    return set(pattern.findall(text))


def test_env_example_documents_the_supported_professor_runtime_contract():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert _documented_keys(text) == EXPECTED_RUNTIME_KEYS
    assert "OPENAI_CONTAINER_BOOTSTRAP_MODEL" not in text


def test_env_example_contains_no_example_openai_key_or_access_token():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert re.search(r"^OPENAI_API_KEY=$", text, re.MULTILINE)
    assert not re.search(r"^OPENAI_API_KEY=.+$", text, re.MULTILINE)
    assert not re.search(r"^APP_ACCESS_TOKEN=.+$", text, re.MULTILINE)
    assert "sk-" not in text


def test_env_example_explains_safe_override_precedence():
    text = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "process environment" in text
    assert "site/.env.local" in text
    assert "optional ignored `site/.env`" in text
    assert "/etc/uconn-isomorphic/environment" in text


def test_local_environment_files_are_ignored():
    patterns = {
        line.strip()
        for line in ROOT_GITIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert ".env" in patterns
    assert ".env.*" in patterns
    assert "!.env.example" in patterns
    assert "!**/.env.example" in patterns
