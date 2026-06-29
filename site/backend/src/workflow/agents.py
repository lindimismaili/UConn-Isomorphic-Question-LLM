"""Build the agents.SDK agent set used by the workflow engine."""

from __future__ import annotations

import os
from typing import Any, Optional

from agents import Agent, CodeInterpreterTool, ModelSettings
from agents.logger import logger
from openai.types.shared.reasoning import Reasoning

from ..helpers import load_agents_config, load_prompt, render_prompt
from ..paths import AGENTS_CONFIG_PATH, LATEX_TEMPLATE_PATH, PROMPTS_DIR
from ..schemas import AGENT_OUTPUT_TYPES


def _load_latex_template() -> str:
    """Read the authoritative LaTeX preamble/macros injected into the latexifier prompt."""
    try:
        return LATEX_TEMPLATE_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.warning("Failed to load LaTeX template %s: %s", LATEX_TEMPLATE_PATH, exc)
        return ""


def _agent_instructions(agent_key: str) -> str:
    """Load an agent's prompt, injecting the LaTeX template for the latexifier."""
    if agent_key == "latexifier":
        return render_prompt(
            PROMPTS_DIR, logger, agent_key, latex_template=_load_latex_template()
        )
    return load_prompt(PROMPTS_DIR, logger, agent_key)


def create_agents(
    *,
    file_ids: Optional[list[str]] = None,
    container_id: Optional[str] = None,
    latex_compile_tool: Any | None = None,
    require_all: bool = True,
) -> dict[str, Agent]:
    """Build the agent set from agents.json.

    Args:
        file_ids: Uploaded file IDs to attach to a code-interpreter auto container.
            Ignored when container_id is provided.
        container_id: Explicit shared container ID used by every Code Interpreter call.
        latex_compile_tool: Optional extra tool to register only on the latex agent.
        require_all: When True, raise if any agent declared in AGENT_OUTPUT_TYPES is
            missing from agents.json.
    """
    file_ids = file_ids or []
    agents_config = load_agents_config(AGENTS_CONFIG_PATH, logger)

    code_interpreter = CodeInterpreterTool(
        tool_config={
            "type": "code_interpreter",
            "container": (
                container_id
                if container_id
                else {"type": "auto", "file_ids": file_ids}
            ),
        }
    )

    valid_reasoning_levels = {"low", "medium", "high", "auto"}
    agents: dict[str, Agent] = {}
    for agent_key, config in agents_config.items():
        output_type = AGENT_OUTPUT_TYPES.get(agent_key)
        if not output_type:
            logger.warning("No output type defined for agent: %s", agent_key)
            continue

        reasoning_level = config.get("reasoning", "none")
        tools = [code_interpreter] if config.get("useCodeInterpreter", False) else []
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
            model=config.get("model", "gpt-5.5"),
            tools=tools,
            output_type=output_type,
            model_settings=ModelSettings(reasoning=reasoning),
        )

    if require_all:
        missing = [k for k in AGENT_OUTPUT_TYPES if k not in agents]
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
    """Clamp OPENAI_CONTAINER_EXPIRES_MINUTES env var to the safe 1-20 range.

    OpenAI containers currently enforce a 20-minute max expiry relative to
    last_active_at.
    """
    raw = os.getenv("OPENAI_CONTAINER_EXPIRES_MINUTES", "20")
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        minutes = 20
    return max(1, min(20, minutes))
