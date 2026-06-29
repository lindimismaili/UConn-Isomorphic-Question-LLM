"""Agent registry: single source of truth loader backed by backend/data/agents.json.

All modules that previously hardcoded agent sequences, names, or set membership
should import from here instead of maintaining their own constants.

Usage:
    from backend.src.registry import agent_sequence, llm_agents, stoppable_agents
    from backend.src.registry import source_file_agents, display_names, all_agents

Note: _load_raw() is lru_cache'd per process. In tests that need a different
registry, call registry._load_raw.cache_clear() before patching AGENTS_CONFIG_PATH.
"""

from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from .paths import AGENTS_CONFIG_PATH


@lru_cache(maxsize=1)
def _load_raw() -> dict[str, Any]:
    """Load and cache agents.json. No side effects at import time."""
    data = json.loads(AGENTS_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"agents.json must be a JSON object; got {type(data)}")
    return data


def _agent_entries() -> list[tuple[str, dict[str, Any]]]:
    """Return (key, entry) pairs for agents only, sorted by sequence field."""
    raw = _load_raw()
    return sorted(
        ((k, v) for k, v in raw.items() if not k.startswith("_")),
        key=lambda item: item[1].get("sequence", 999),
    )


# ── Primary accessors ────────────────────────────────────────────────────────

def all_agents() -> dict[str, dict[str, Any]]:
    """Full registry dict (key → entry) ordered by sequence."""
    return dict(_agent_entries())


def agent_entry(key: str) -> dict[str, Any]:
    """Return the registry entry for one agent key, or raise KeyError."""
    raw = _load_raw()
    entry = {k: v for k, v in raw.items() if not k.startswith("_")}.get(key)
    if entry is None:
        raise KeyError(f"Unknown agent key: {key!r}")
    return entry


def agent_sequence() -> list[str]:
    """Ordered list of all agent keys. Replaces WORKFLOW_AGENT_SEQUENCE."""
    return [key for key, _ in _agent_entries()]


def display_names() -> dict[str, str]:
    """Map of agent key → display name. Replaces AGENT_DISPLAY_NAMES."""
    return {key: entry["name"] for key, entry in _agent_entries()}


# ── Filtered views ────────────────────────────────────────────────────────────

def llm_agents() -> tuple[str, ...]:
    """Keys for LLM agents (isLLM=true). Replaces _LLM_AGENTS."""
    return tuple(key for key, e in _agent_entries() if e.get("isLLM"))


def stoppable_agents() -> tuple[str, ...]:
    """Keys for agents usable as stop_after targets. Replaces _stoppable_agents."""
    return tuple(key for key, e in _agent_entries() if e.get("isStoppable"))


def source_file_agents() -> frozenset[str]:
    """Keys for agents that accept uploaded source documents. Replaces SOURCE_FILE_INPUT_AGENTS."""
    return frozenset(key for key, e in _agent_entries() if e.get("acceptsSourceFiles"))


def promptable_agents() -> list[str]:
    """Keys/names for agents with a prompt file, for the allowed_prompts allowlist.

    Returns the promptName when present (e.g. 'evaluator' for evaluation), else the key.
    """
    return [
        entry.get("promptName") or key
        for key, entry in _agent_entries()
        if entry.get("hasPrompt")
    ]


def aliases_map() -> dict[str, str]:
    """Flat alias → canonical key map built from each entry's aliases array."""
    result: dict[str, str] = {}
    for key, entry in _agent_entries():
        for alias in entry.get("aliases") or []:
            result[alias] = key
    return result


def input_to_agent_sequence() -> list[str]:
    """All runnable stages (all agents except the terminal output node)."""
    return agent_sequence()[:-1]


def cli_descriptions() -> dict[str, str]:
    """Map of agent key → CLI description. Replaces _AGENT_DESCRIPTIONS."""
    return {key: entry.get("cliDescription", "") for key, entry in _agent_entries()}


def count_labels() -> dict[str, str]:
    """Map of agent key → count log label (non-null only). Replaces AGENT_COUNT_LOGS."""
    return {
        key: entry["countLabel"]
        for key, entry in _agent_entries()
        if entry.get("countLabel")
    }


# ── Setup / container accessors ──────────────────────────────────────────────

def setup_bootstrap_model() -> str:
    """Model used for container bootstrap shell tasks."""
    return agent_entry("setup").get("model", "gpt-5.5")


def setup_allowed_domains() -> list[str]:
    """Default container network allowlist. Replaces DEFAULT_ALLOWED_DOMAINS."""
    return list(agent_entry("setup").get("allowedDomains", []))


def setup_container_packages() -> list[str]:
    """Python packages installed inside the container. Replaces CONTAINER_PYTHON_PACKAGES."""
    return list(agent_entry("setup").get("containerPythonPackages", []))


# ── Evaluation accessor ───────────────────────────────────────────────────────

def evaluation_model() -> str:
    """Model used for evaluation/grading. Authoritative source for the eval agent."""
    return agent_entry("evaluation").get("model", "gpt-5.5")


# ── Config/schema accessors ───────────────────────────────────────────────────

def themes() -> list[dict]:
    """UI dropdown themes as ordered list of {label, prompt} dicts."""
    config = _load_raw().get("_config", {})
    return list(config.get("themes", {}).values())


def api_title() -> str:
    """FastAPI application title."""
    return _load_raw().get("_config", {}).get("apiTitle", "Isomorphic Question Generator API")


def api_description() -> str:
    """FastAPI application description."""
    return _load_raw().get("_config", {}).get("apiDescription", "Generate isomorphic physics questions using AI agents")


def default_from_agent() -> str:
    """Default starting agent for CLI --from-agent. Returns the first stoppable agent."""
    stoppable = stoppable_agents()
    return stoppable[0] if stoppable else "normalizer"


def agent_result_field(key: str) -> str | None:
    """Return the WorkflowResult field name for a stoppable agent's primary output, or None."""
    raw = _load_raw()
    entry = {k: v for k, v in raw.items() if not k.startswith("_")}.get(key, {})
    return entry.get("resultField")
