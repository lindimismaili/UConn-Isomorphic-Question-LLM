"""Build the agents.SDK agent set used by the workflow engine."""

from __future__ import annotations

import os
from collections.abc import Collection
from typing import Any, Optional

from agents import Agent, CodeInterpreterTool, ModelSettings
from agents.logger import logger
from openai.types.shared.reasoning import Reasoning

from ..helpers import load_prompt, render_prompt
from ..paths import LATEX_TEMPLATE_PATH, PROMPTS_DIR
from ..registry import all_agents as _registry_agents
from ..registry import promptable_agents
from ..schemas import AGENT_OUTPUT_TYPES


def _load_latex_template() -> str:
    """Read the authoritative LaTeX preamble/macros injected into the latexifier prompt."""
    if not LATEX_TEMPLATE_PATH.is_file():
        raise FileNotFoundError(f"Required LaTeX template not found: {LATEX_TEMPLATE_PATH}")
    try:
        template = LATEX_TEMPLATE_PATH.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError(
            f"Required LaTeX template is unreadable: {LATEX_TEMPLATE_PATH}"
        ) from exc
    if not template:
        raise ValueError(f"Required LaTeX template is empty: {LATEX_TEMPLATE_PATH}")
    return template


def generation_strategy() -> str:
    """Return the active generator strategy from GENERATION_STRATEGY.

    "holistic" (default) re-casts each source question in one pass; "decompose"
    first parses each source question into structured components and generates
    from that breakdown. Invalid values fail local preflight before paid work.
    """
    raw = (os.getenv("GENERATION_STRATEGY", "holistic") or "").strip().lower()
    if raw not in {"holistic", "decompose"}:
        raise ValueError(
            "GENERATION_STRATEGY must be either 'holistic' or 'decompose'."
        )
    return raw


def validate_workflow_assets() -> None:
    """Validate every prompt/template needed by a paid workflow.

    This deliberately performs only local reads and placeholder substitution.
    The input guardrail calls it before the first provider operation, while the
    individual consumers still load their assets again at point of use.
    """
    container_expires_minutes()
    latex_template = _load_latex_template()
    for prompt_name in promptable_agents():
        if prompt_name == "latexifier":
            render_prompt(
                PROMPTS_DIR,
                logger,
                prompt_name,
                latex_template=latex_template,
            )
        else:
            load_prompt(PROMPTS_DIR, logger, prompt_name)

    if generation_strategy() == "decompose":
        load_prompt(PROMPTS_DIR, logger, "generator_decompose")

    render_prompt(
        PROMPTS_DIR,
        logger,
        "bootstrap",
        domains_json="[]",
        packages="package-name",
    )
    render_prompt(
        PROMPTS_DIR,
        logger,
        "bootstrapCompile",
        tex_path="/mnt/data/source.tex",
        basename="questions",
    )


def _agent_instructions(agent_key: str) -> str:
    """Load an agent's prompt, injecting the LaTeX template for the latexifier."""
    if agent_key == "latexifier":
        return render_prompt(
            PROMPTS_DIR, logger, agent_key, latex_template=_load_latex_template()
        )
    if agent_key == "generator" and generation_strategy() == "decompose":
        return load_prompt(PROMPTS_DIR, logger, "generator_decompose")
    return load_prompt(PROMPTS_DIR, logger, agent_key)


def create_agents(
    *,
    container_id: Optional[str] = None,
    latex_compile_tool: Any | None = None,
    code_interpreter_enabled: bool = True,
    agent_keys: Collection[str] | None = None,
    require_all: bool = True,
) -> dict[str, Agent]:
    """Build the agent set from agents.json.

    Args:
        container_id: Explicit owned container ID for the selected scope's
            Code Interpreter calls.
        latex_compile_tool: Optional extra tool to register only on the latex agent.
        code_interpreter_enabled: When False, omit Code Interpreter tools even when
            registry metadata enables them. The workflow engine uses this only for
            the pre-content-guardrail agent set when no container may exist yet.
        agent_keys: Optional exact stage subset to build. Container trust scopes use
            this so extraction and downstream agents cannot cross-bind by accident.
        require_all: When True, raise if any agent declared in AGENT_OUTPUT_TYPES is
            missing from agents.json (or from the requested subset).
    """
    agents_config = _registry_agents()
    selected_keys = (
        set(AGENT_OUTPUT_TYPES)
        if agent_keys is None
        else set(agent_keys)
    )
    unknown_keys = sorted(selected_keys.difference(AGENT_OUTPUT_TYPES))
    if unknown_keys:
        raise ValueError(f"Unknown agent key(s): {', '.join(unknown_keys)}")
    selected_uses_code_interpreter = any(
        bool((agents_config.get(agent_key) or {}).get("useCodeInterpreter"))
        for agent_key in selected_keys
    )
    if (
        code_interpreter_enabled
        and selected_uses_code_interpreter
        and not container_id
    ):
        raise ValueError(
            "Code Interpreter agents require an explicit owned container_id"
        )
    code_interpreter = (
        CodeInterpreterTool(
            tool_config={
                "type": "code_interpreter",
                "container": container_id,
            }
        )
        if code_interpreter_enabled and selected_uses_code_interpreter
        else None
    )

    valid_reasoning_levels = {"low", "medium", "high", "auto"}
    agents: dict[str, Agent] = {}
    for agent_key, output_type in AGENT_OUTPUT_TYPES.items():
        if agent_key not in selected_keys:
            continue
        config = agents_config.get(agent_key)
        if config is None:
            continue

        reasoning_level = config.get("reasoning", "none")
        tools = (
            [code_interpreter]
            if code_interpreter is not None
            and config.get("useCodeInterpreter", False)
            else []
        )
        if agent_key == "latexifier" and latex_compile_tool is not None:
            tools.append(latex_compile_tool)
        reasoning = (
            Reasoning(effort=reasoning_level)
            if reasoning_level in valid_reasoning_levels
            else None
        )
        agents[agent_key] = Agent(
            name=config.get("name", agent_key.title()),
            instructions=_agent_instructions(agent_key),
            model=config.get("model", "gpt-5.6-luna"),
            tools=tools,
            output_type=output_type,
            model_settings=ModelSettings(
                max_tokens=int(config["maxOutputTokens"]),
                parallel_tool_calls=False,
                reasoning=reasoning,
                store=False,
            ),
        )

    if require_all:
        missing = [k for k in selected_keys if k not in agents]
        if missing:
            raise ValueError(f"Missing required agent config(s): {', '.join(missing)}")
    return agents


def any_agent_uses_code_interpreter(agents_config: dict[str, Any]) -> bool:
    """Return True when any configured agent requests the code interpreter."""
    for _agent_key, cfg in agents_config.items():
        try:
            if bool(cfg.get("useCodeInterpreter", False)):
                return True
        except Exception:
            continue
    return False


def container_expires_minutes() -> int:
    """Validate OPENAI_CONTAINER_EXPIRES_MINUTES in the provider's 1-20 range.

    OpenAI containers currently enforce a 20-minute max expiry relative to
    last_active_at.
    """
    raw = (os.getenv("OPENAI_CONTAINER_EXPIRES_MINUTES", "20") or "").strip()
    try:
        minutes = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "OPENAI_CONTAINER_EXPIRES_MINUTES must be an integer from 1 to 20."
        ) from exc
    if not 1 <= minutes <= 20:
        raise ValueError(
            "OPENAI_CONTAINER_EXPIRES_MINUTES must be an integer from 1 to 20."
        )
    return minutes
