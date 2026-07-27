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
import math
import os
import re
from functools import lru_cache
from typing import Any

from .paths import AGENTS_CONFIG_PATH

_AGENT_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_REQUIRED_ENTRY_FIELDS = {
    "acceptsSourceFiles",
    "aliases",
    "cliDescription",
    "countLabel",
    "description",
    "hasPrompt",
    "inputFrom",
    "isLLM",
    "isStoppable",
    "kind",
    "model",
    "name",
    "outputTo",
    "reasoning",
    "sequence",
    "useCodeInterpreter",
}
_BOOLEAN_ENTRY_FIELDS = {
    "acceptsSourceFiles",
    "hasPrompt",
    "isLLM",
    "isStoppable",
    "useCodeInterpreter",
}
_VALID_KINDS = {"agent", "guardrail", "runtime"}
_EXPECTED_PIPELINE_NODES = {
    "setup",
    "input_guardrail",
    "normalizer",
    "extracter",
    "content_guardrail",
    "verifier",
    "generator",
    "latexifier",
    "evaluation",
    "output",
}
_VALID_REASONING = {"none", "low", "medium", "high", "auto"}
_AGENTS_SDK_NODE_KEYS = {
    "normalizer",
    "extracter",
    "verifier",
    "generator",
    "latexifier",
}
_MIN_MODEL_OUTPUT_TOKENS = 256
_MAX_MODEL_OUTPUT_TOKENS = 131_072
_MAX_THEME_LABEL_CHARS = 100
_MAX_THEME_PROMPT_CHARS = 10_000


def _validate_registry(data: dict[str, Any]) -> None:
    """Fail fast when the hand-curated registry violates pipeline invariants."""
    config = data.get("_config")
    if not isinstance(config, dict):
        raise ValueError("agents.json must contain an object-valued _config entry")

    configured_themes = config.get("themes")
    if not isinstance(configured_themes, dict):
        raise ValueError("_config.themes must be a JSON object")
    seen_theme_labels: set[str] = set()
    for key, theme in configured_themes.items():
        if not isinstance(key, str) or not _AGENT_KEY_PATTERN.fullmatch(key):
            raise ValueError("Theme keys must be lowercase snake_case strings")
        if not isinstance(theme, dict):
            raise ValueError(f"Theme {key!r} must be a JSON object")
        label = theme.get("label")
        prompt = theme.get("prompt")
        if (
            not isinstance(label, str)
            or not label.strip()
            or len(label.strip()) > _MAX_THEME_LABEL_CHARS
        ):
            raise ValueError(
                f"Theme {key!r} label must be a non-empty string no longer than "
                f"{_MAX_THEME_LABEL_CHARS} characters"
            )
        if (
            not isinstance(prompt, str)
            or not prompt.strip()
            or len(prompt.strip()) > _MAX_THEME_PROMPT_CHARS
        ):
            raise ValueError(
                f"Theme {key!r} prompt must be a non-empty string no longer than "
                f"{_MAX_THEME_PROMPT_CHARS} characters"
            )
        normalized_label = label.strip().casefold()
        if normalized_label in seen_theme_labels:
            raise ValueError(f"Theme label {label.strip()!r} is duplicated")
        seen_theme_labels.add(normalized_label)

    entries = {key: value for key, value in data.items() if not key.startswith("_")}
    if not entries:
        raise ValueError("agents.json must define at least one pipeline node")
    if set(entries) != _EXPECTED_PIPELINE_NODES:
        missing = sorted(_EXPECTED_PIPELINE_NODES - set(entries))
        unexpected = sorted(set(entries) - _EXPECTED_PIPELINE_NODES)
        raise ValueError(
            "agents.json pipeline nodes do not match the engine contract; "
            f"missing={missing}, unexpected={unexpected}"
        )

    seen_sequences: dict[float, str] = {}
    canonical_keys = set(entries)
    seen_aliases: dict[str, str] = {}

    for key, entry in entries.items():
        if not _AGENT_KEY_PATTERN.fullmatch(key):
            raise ValueError(f"Invalid agent key {key!r}; use lowercase snake_case")
        if not isinstance(entry, dict):
            raise ValueError(f"Agent {key!r} must be a JSON object")

        missing = sorted(_REQUIRED_ENTRY_FIELDS - entry.keys())
        if missing:
            raise ValueError(f"Agent {key!r} is missing required fields: {', '.join(missing)}")

        for field in _BOOLEAN_ENTRY_FIELDS:
            if not isinstance(entry[field], bool):
                raise ValueError(f"Agent {key!r} field {field!r} must be a boolean")

        kind = entry["kind"]
        if kind not in _VALID_KINDS:
            raise ValueError(
                f"Agent {key!r} has invalid kind {kind!r}; expected one of {sorted(_VALID_KINDS)}"
            )
        if kind == "agent" and not entry["isLLM"]:
            raise ValueError(f"Agent node {key!r} must set isLLM=true")
        if kind != "agent" and entry["isLLM"]:
            raise ValueError(f"{kind.title()} node {key!r} must set isLLM=false")
        if kind != "agent" and entry["isStoppable"]:
            raise ValueError(f"{kind.title()} node {key!r} cannot be stoppable")

        sequence = entry["sequence"]
        if (
            isinstance(sequence, bool)
            or not isinstance(sequence, (int, float))
            or not math.isfinite(float(sequence))
        ):
            raise ValueError(f"Agent {key!r} sequence must be a finite number")
        normalized_sequence = float(sequence)
        if normalized_sequence in seen_sequences:
            raise ValueError(
                f"Agents {seen_sequences[normalized_sequence]!r} and {key!r} "
                f"share sequence {sequence!r}"
            )
        seen_sequences[normalized_sequence] = key

        aliases = entry["aliases"]
        if not isinstance(aliases, list) or any(
            not isinstance(alias, str) or not _AGENT_KEY_PATTERN.fullmatch(alias)
            for alias in aliases
        ):
            raise ValueError(f"Agent {key!r} aliases must be lowercase snake_case strings")
        for alias in aliases:
            if alias in canonical_keys:
                raise ValueError(f"Alias {alias!r} collides with a canonical agent key")
            previous = seen_aliases.get(alias)
            if previous is not None:
                raise ValueError(
                    f"Alias {alias!r} is assigned to both {previous!r} and {key!r}"
                )
            seen_aliases[alias] = key

        for field in (
            "cliDescription",
            "description",
            "inputFrom",
            "model",
            "name",
            "outputTo",
            "reasoning",
        ):
            if not isinstance(entry[field], str):
                raise ValueError(f"Agent {key!r} field {field!r} must be a string")
        if entry["reasoning"] not in _VALID_REASONING:
            raise ValueError(
                f"Agent {key!r} reasoning must be one of {sorted(_VALID_REASONING)}"
            )
        if kind == "agent" and not entry["model"].strip():
            raise ValueError(f"Agent node {key!r} must configure a model")
        if entry["isStoppable"] and not isinstance(entry.get("resultField"), str):
            raise ValueError(f"Stoppable agent {key!r} must configure resultField")
        if "countField" in entry and not isinstance(entry["countField"], str):
            raise ValueError(f"Agent {key!r} countField must be a string when present")
        if "maxTurns" in entry:
            max_turns = entry["maxTurns"]
            if (
                isinstance(max_turns, bool)
                or not isinstance(max_turns, int)
                or not 1 <= max_turns <= 50
            ):
                raise ValueError(f"Agent {key!r} maxTurns must be an integer from 1 to 50")
        if key in _AGENTS_SDK_NODE_KEYS:
            max_output_tokens = entry.get("maxOutputTokens")
            if (
                isinstance(max_output_tokens, bool)
                or not isinstance(max_output_tokens, int)
                or not _MIN_MODEL_OUTPUT_TOKENS
                <= max_output_tokens
                <= _MAX_MODEL_OUTPUT_TOKENS
            ):
                raise ValueError(
                    f"Agent {key!r} maxOutputTokens must be an integer from "
                    f"{_MIN_MODEL_OUTPUT_TOKENS} to {_MAX_MODEL_OUTPUT_TOKENS}"
                )
        if key == "setup":
            max_output_tokens = entry.get("maxOutputTokens")
            if (
                isinstance(max_output_tokens, bool)
                or not isinstance(max_output_tokens, int)
                or not _MIN_MODEL_OUTPUT_TOKENS
                <= max_output_tokens
                <= _MAX_MODEL_OUTPUT_TOKENS
            ):
                raise ValueError(
                    "Agent 'setup' maxOutputTokens must be an integer from "
                    f"{_MIN_MODEL_OUTPUT_TOKENS} to {_MAX_MODEL_OUTPUT_TOKENS}"
                )

    ordered_keys = [
        key for _, key in sorted((sequence, key) for sequence, key in seen_sequences.items())
    ]
    if ordered_keys[0] != "setup" or entries["setup"]["kind"] != "runtime":
        raise ValueError("setup must be the first pipeline node and have kind='runtime'")
    if ordered_keys[-1] != "output" or entries["output"]["kind"] != "runtime":
        raise ValueError("output must be the final pipeline node and have kind='runtime'")
    if entries["evaluation"]["kind"] != "agent":
        raise ValueError("evaluation must have kind='agent'")

    guardrails = config.get("guardrails")
    if not isinstance(guardrails, dict):
        raise ValueError("_config.guardrails must be a JSON object")
    required_guardrail_fields = {
        "enabled",
        "model",
        "moderationModel",
        "reasoning",
        "maxOutputTokens",
        "physicsMinConfidence",
        "blockOffTopic",
        "failClosed",
        "maxQuestionsSampled",
        "inputNode",
        "contentNode",
    }
    missing_guardrail_fields = sorted(required_guardrail_fields - guardrails.keys())
    if missing_guardrail_fields:
        raise ValueError(
            "_config.guardrails is missing required fields: "
            + ", ".join(missing_guardrail_fields)
        )
    for field in ("enabled", "blockOffTopic", "failClosed"):
        if not isinstance(guardrails[field], bool):
            raise ValueError(f"_config.guardrails.{field} must be a boolean")
    for field in ("model", "moderationModel"):
        if not isinstance(guardrails[field], str) or not guardrails[field].strip():
            raise ValueError(f"_config.guardrails.{field} must be a non-empty string")
    if guardrails["reasoning"] not in _VALID_REASONING:
        raise ValueError(
            f"_config.guardrails.reasoning must be one of {sorted(_VALID_REASONING)}"
        )
    guardrail_max_output_tokens = guardrails["maxOutputTokens"]
    if (
        isinstance(guardrail_max_output_tokens, bool)
        or not isinstance(guardrail_max_output_tokens, int)
        or not _MIN_MODEL_OUTPUT_TOKENS
        <= guardrail_max_output_tokens
        <= _MAX_MODEL_OUTPUT_TOKENS
    ):
        raise ValueError(
            "_config.guardrails.maxOutputTokens must be an integer from "
            f"{_MIN_MODEL_OUTPUT_TOKENS} to {_MAX_MODEL_OUTPUT_TOKENS}"
        )
    confidence = guardrails["physicsMinConfidence"]
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not math.isfinite(float(confidence))
        or not 0 <= float(confidence) <= 1
    ):
        raise ValueError("_config.guardrails.physicsMinConfidence must be between 0 and 1")
    sampled = guardrails["maxQuestionsSampled"]
    if (
        isinstance(sampled, bool)
        or not isinstance(sampled, int)
        or not 1 <= sampled <= 100
    ):
        raise ValueError(
            "_config.guardrails.maxQuestionsSampled must be an integer from 1 to 100"
        )
    for field in ("inputNode", "contentNode"):
        node_key = guardrails.get(field)
        if node_key not in entries or entries[node_key]["kind"] != "guardrail":
            raise ValueError(
                f"_config.guardrails.{field} must reference a guardrail node"
            )
    input_node = guardrails["inputNode"]
    content_node = guardrails["contentNode"]
    if input_node == content_node:
        raise ValueError("Input and content guardrails must use distinct nodes")
    if not (
        ordered_keys.index("setup")
        < ordered_keys.index(input_node)
        < ordered_keys.index("normalizer")
    ):
        raise ValueError("The input guardrail must be ordered between setup and normalizer")
    if not (
        ordered_keys.index("extracter")
        < ordered_keys.index(content_node)
        < ordered_keys.index("verifier")
    ):
        raise ValueError("The content guardrail must be ordered between extracter and verifier")


@lru_cache(maxsize=1)
def _load_raw() -> dict[str, Any]:
    """Load and cache agents.json. No side effects at import time."""
    data = json.loads(AGENTS_CONFIG_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"agents.json must be a JSON object; got {type(data)}")
    _validate_registry(data)
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
    """Runnable start-from targets: model-driven agent stages only.

    Runtime and guardrail nodes are orchestration boundaries, not independently
    runnable model inputs.  Excluding both prevents replay endpoints from
    starting inside setup/teardown or bypassing safety checks.
    """
    return [key for key, entry in _agent_entries() if entry.get("kind") == "agent"]


# ── Guardrail accessors ───────────────────────────────────────────────────────

def _guardrails_config() -> dict[str, Any]:
    """Return the _config.guardrails block (empty dict when absent)."""
    config = _load_raw().get("_config", {}).get("guardrails", {})
    return config if isinstance(config, dict) else {}


def guardrail_nodes() -> list[str]:
    """Keys for guardrail display nodes (kind == "guardrail"), in sequence order."""
    return [key for key, entry in _agent_entries() if entry.get("kind") == "guardrail"]


def guardrails_enabled() -> bool:
    """Whether the guardrail layer runs. GUARDRAILS_ENABLED env overrides config."""
    env = os.getenv("GUARDRAILS_ENABLED")
    if env is not None:
        normalized = env.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise ValueError(
            "GUARDRAILS_ENABLED must be one of: 1, 0, true, false, yes, no, on, off"
        )
    return bool(_guardrails_config().get("enabled", True))


def guardrail_model() -> str:
    """Model used by the LLM guardrail classifiers."""
    return _guardrails_config().get("model") or evaluation_model()


def guardrail_moderation_model() -> str:
    """Moderation model used by the deterministic content pre-filter."""
    return _guardrails_config().get("moderationModel", "omni-moderation-latest")


def guardrail_reasoning() -> str:
    """Reasoning effort for the guardrail classifier agents."""
    return _guardrails_config().get("reasoning", "low")


def guardrail_max_output_tokens() -> int:
    """Maximum generated tokens for each guardrail classifier call."""
    return int(_guardrails_config().get("maxOutputTokens", 2048))


def guardrail_physics_min_confidence() -> float:
    """Confidence floor above which a non-physics classification blocks (off-topic)."""
    value = float(_guardrails_config().get("physicsMinConfidence", 0.75))
    if not 0.0 <= value <= 1.0:
        raise ValueError("guardrails.physicsMinConfidence must be between 0 and 1")
    return value


def guardrail_block_off_topic() -> bool:
    """Whether off-topic (non-physics but genuine question) content is blocked."""
    return bool(_guardrails_config().get("blockOffTopic", True))


def guardrail_fail_closed() -> bool:
    """Whether a guardrail infrastructure error blocks the run (vs. fails open)."""
    return bool(_guardrails_config().get("failClosed", True))


def guardrail_max_questions_sampled() -> int:
    """Maximum questions sent in each content-classifier batch."""
    value = int(_guardrails_config().get("maxQuestionsSampled", 40))
    if not 1 <= value <= 100:
        raise ValueError("guardrails.maxQuestionsSampled must be between 1 and 100")
    return value


def guardrail_input_node() -> str:
    """Registry key for the input-stage guardrail display node."""
    return str(_guardrails_config().get("inputNode", "input_guardrail"))


def guardrail_content_node() -> str:
    """Registry key for the content-stage guardrail display node."""
    return str(_guardrails_config().get("contentNode", "content_guardrail"))


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
    return agent_entry("setup").get("model", "gpt-5.6-luna")


def setup_max_output_tokens() -> int:
    """Maximum generated tokens for each container bootstrap or compile shell task."""
    return int(agent_entry("setup")["maxOutputTokens"])


def setup_allowed_domains() -> list[str]:
    """Default container network allowlist. Replaces DEFAULT_ALLOWED_DOMAINS."""
    return list(agent_entry("setup").get("allowedDomains", []))


def setup_container_packages() -> list[str]:
    """Python packages installed inside the container. Replaces CONTAINER_PYTHON_PACKAGES."""
    return list(agent_entry("setup").get("containerPythonPackages", []))


# ── Evaluation accessor ───────────────────────────────────────────────────────

def evaluation_model() -> str:
    """Model used for evaluation/grading. Authoritative source for the eval agent."""
    return agent_entry("evaluation").get("model", "gpt-5.6-luna")


def evaluation_agent() -> str:
    """Canonical key for the configured evaluation stage."""
    return "evaluation"


# ── Config/schema accessors ───────────────────────────────────────────────────

def themes() -> list[dict]:
    """UI dropdown themes as ordered list of {label, prompt} dicts."""
    config = _load_raw().get("_config", {})
    return list(config.get("themes", {}).values())


def canonical_theme(value: str | None) -> str:
    """Resolve a client theme selector to server-owned canonical guidance.

    Accepted non-empty selectors are a configured key, a configured label
    (both case-insensitive), or the exact ``"Label: prompt"`` value returned by
    the frontend. A caller may not keep the label while replacing its prompt.
    """
    requested = str(value or "").strip()
    if not requested:
        return ""

    configured = _load_raw().get("_config", {}).get("themes", {})
    requested_identifier = requested.casefold()
    for key, theme in configured.items():
        label = str(theme["label"]).strip()
        prompt = str(theme["prompt"]).strip()
        canonical = f"{label}: {prompt}"
        if requested == canonical:
            return canonical
        if requested_identifier in {str(key).casefold(), label.casefold()}:
            return canonical

    raise ValueError(
        "Unknown or mismatched theme. Select a theme from the server-provided list."
    )


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
