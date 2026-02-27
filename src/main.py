"""
Isomorphic Question Generation Workflow

This module orchestrates the agent workflow for generating isomorphic physics questions.
All heavy lifting (PDF parsing, text extraction, etc.) is done by agents with tools.
Local code only handles workflow orchestration.
"""

import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from agents import (
    Agent,
    CodeInterpreterTool,
    ModelSettings,
    Runner,
    RunConfig,
    trace,
)
from agents.logger import logger
from openai import OpenAI
from openai.types.shared.reasoning import Reasoning
from pydantic import BaseModel
from evaluation import run_openai_output_evaluation

# =============================================================================
# Configuration
# =============================================================================

THIS_DIR = Path(__file__).resolve().parent


def _detect_repo_root(start: Path) -> Path:
    candidates = [start, start.parent]
    for candidate in candidates:
        if (candidate / "data" / "prompts").is_dir():
            return candidate
    return start


REPO_ROOT = _detect_repo_root(THIS_DIR)
PROJECT_ROOT = REPO_ROOT
ENV_PATH = REPO_ROOT / ".env"
DATA_DIR = REPO_ROOT / "data"
PROMPTS_DIR = DATA_DIR / "prompts"
AGENTS_CONFIG_PATH = DATA_DIR / "agents.json"
QUESTION_DATA_DIR = REPO_ROOT / "Question Data Files"
if not QUESTION_DATA_DIR.exists():
    QUESTION_DATA_DIR = REPO_ROOT / "archive" / "Question Data Files"


def _load_dotenv(path: Path) -> None:
    """Load environment variables from .env file."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('\'"')
        os.environ.setdefault(key, value)


_load_dotenv(ENV_PATH)

# Initialize OpenAI client for file uploads
client = OpenAI()

THEME_ALLOWED_VALUES = {
    "life_science",
    "engineering",
    "everyday_activity",
    "physics_abstract",
    "",
}


def load_agents_config() -> dict:
    """Load agent configuration from agents.json."""
    if not AGENTS_CONFIG_PATH.exists():
        logger.warning("Agents config not found: %s", AGENTS_CONFIG_PATH)
        return {}
    try:
        return json.loads(AGENTS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load agents config: %s", exc)
        return {}


def load_prompt(name: str) -> str:
    """Load a prompt from the prompts directory."""
    prompt_path = PROMPTS_DIR / f"{name}.txt"
    if not prompt_path.exists():
        logger.warning("Prompt file not found: %s", prompt_path)
        return ""
    try:
        return prompt_path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to load prompt %s: %s", name, exc)
        return ""


# =============================================================================
# File Upload Utilities
# =============================================================================


def upload_file_to_openai(file_path: Path) -> str:
    """Upload a file to OpenAI and return the file ID."""
    with open(file_path, "rb") as f:
        response = client.files.create(file=f, purpose="assistants")
    logger.info("Uploaded file %s -> %s", file_path.name, response.id)
    return response.id



# =============================================================================
# Agent Runner Utility
# =============================================================================


async def run_agent(
    agent: Agent,
    inputs: list[dict[str, Any]],
    run_config: RunConfig,
    agent_outputs: list[dict[str, Any]],
    received_from: str,
    sent_to: Optional[str],
) -> Any:
    """
    Run an agent and log the results.
    
    Args:
        agent: The agent to run
        inputs: Input messages for the agent
        run_config: Configuration for the run
        agent_outputs: Aggregate list to collect agent outputs
        received_from: Name of the previous agent in workflow
        sent_to: Name of the next agent in workflow
    
    Returns:
        The agent run result
    """
    logger.info("Running agent: %s", agent.name)
    start_time = time.time()

    result = await Runner.run(agent, input=inputs, run_config=run_config)

    end_time = time.time()
    elapsed = end_time - start_time
    
    # Extract reasoning level if available
    reasoning_level = None
    if agent.model_settings and agent.model_settings.reasoning:
        reasoning_level = agent.model_settings.reasoning.effort
    
    # Build log payload
    input_label = "USER INPUT" if received_from == "user" else f"Input From Agent {received_from.capitalize()}"
    output_label = "END USER" if sent_to is None else f"Output To Agent {sent_to.capitalize()}"
    output_obj = (
        result.final_output.model_dump()
        if hasattr(result.final_output, "model_dump")
        else result.final_output
    )
    
    log_payload = {
        "agent_name": agent.name,
        "model": agent.model,
        "reasoning_level": reasoning_level,
        "execution_time_seconds": elapsed,
        "input_summary": f"{len(inputs)} message(s)",
        "input_messages": inputs,
        "output_as_text": result.final_output.model_dump_json() if hasattr(result.final_output, "model_dump_json") else str(result.final_output),
        "output_object": output_obj,
        "agent_received_from": input_label,
        "received_from_node": received_from,
        "agent_sent_to": output_label,
        "sent_to_node": sent_to or "user",
    }
    
    # Append log to aggregate output
    agent_outputs.append(log_payload)
    
    logger.info("Completed: %s (%.2fs)", agent.name, elapsed)
    return result

# =============================================================================
# Output Schemas
# =============================================================================


class OptionItem(BaseModel):
    """A single option in a multiple-choice question."""
    key: str
    text: str
    is_correct: bool
    explanation: str


class SourceQuestionItem(BaseModel):
    """A question extracted from source material."""
    source_id: str
    source_raw: str
    stem: str
    options: list[OptionItem]
    has_explicit_answer: bool
    difficulty: str
    question_type: str
    theme: str = ""
    topic_tags: list[str]


class NewQuestionItem(BaseModel):
    """A newly generated isomorphic question."""
    id: int
    source_id: str
    stem: str
    options: list[OptionItem]
    difficulty: str
    theme: str = ""
    topic_tags: list[str]
    generation_notes: str


class NormalizerOutput(BaseModel):
    """Output from the Normalizer agent."""
    new_questions_per_question: int = 1
    difficulty: str = ""
    theme: str = ""
    title: str = ""
    notes: str = ""


class ExtracterOutput(BaseModel):
    """Output from the Extracter agent."""
    filename: str = ""
    title: str = ""
    author: str = ""
    date: str = ""
    course: str = ""
    questions: list[SourceQuestionItem]


class VerifierOutput(BaseModel):
    """Output from the Verifier agent."""
    questions: list[SourceQuestionItem]


class GeneratorOutput(BaseModel):
    """Output from the Generator agent."""
    isomorphic_questions: list[NewQuestionItem]


class LaTeXOutput(BaseModel):
    """Output from the LaTeX Formatter agent."""
    latex_content: str
    filename: str


# =============================================================================
# Agent Definitions
# =============================================================================

# Output type mapping for each agent
AGENT_OUTPUT_TYPES = {
    "normalizer": NormalizerOutput,
    "extracter": ExtracterOutput,
    "verifier": VerifierOutput,
    "generator": GeneratorOutput,
    "latex": LaTeXOutput,
}


def create_agents(
    *,
    file_ids: Optional[list[str]] = None,
    container_id: Optional[str] = None,
) -> dict[str, Agent]:
    """
    Create all agents with their tools configured from agents.json.
    
    Args:
        file_ids: List of uploaded file IDs for code interpreter access
        container_id: If provided, use a single explicit Code Interpreter container
            shared across all agent calls in the workflow.
    
    Returns:
        Dictionary of agent key -> Agent instance
    """
    file_ids = file_ids or []
    agents_config = load_agents_config()
    
    # Code interpreter tool with file access
    code_interpreter = CodeInterpreterTool(
        tool_config={
            "type": "code_interpreter",
            "container": (
                container_id
                if container_id
                else {
                    "type": "auto",
                    "file_ids": file_ids,
                }
            ),
        }
    )
    
    agents = {}
    
    for agent_key, config in agents_config.items():
        # Get output type for this agent
        output_type = AGENT_OUTPUT_TYPES.get(agent_key)
        if not output_type:
            logger.warning("No output type defined for agent: %s", agent_key)
            continue
        
        # Determine tools based on config
        tools = [code_interpreter] if config.get("useCodeInterpreter", False) else []
        
        # Get reasoning level from config
        reasoning_level = config.get("reasoning", "none")
        valid_reasoning_levels = {"low", "medium", "high", "auto"}
        reasoning = (
            Reasoning(effort=reasoning_level)
            if reasoning_level in valid_reasoning_levels
            else None
        )

        # Create the agent
        agent = Agent(
            name=config.get("name", agent_key.title()),
            instructions=load_prompt(agent_key),
            model=config.get("model", "gpt-4o"),
            tools=tools if tools else None,
            output_type=output_type,
            model_settings=ModelSettings(
                reasoning=reasoning,
            ),
        )
        
        agents[agent_key] = agent
        logger.info("Created agent: %s (model=%s, reasoning=%s)", 
                   config.get("name"), config.get("model"), reasoning_level)
    
    return agents


def _any_agent_uses_code_interpreter(agents_config: dict[str, Any]) -> bool:
    for _agent_key, cfg in agents_config.items():
        try:
            if bool(cfg.get("useCodeInterpreter", False)):
                return True
        except Exception:
            continue
    return False


def _container_expires_minutes() -> int:
    """
    OpenAI containers currently enforce a max expiry of 20 minutes
    (relative to last_active_at). Clamp env config to a safe range.
    """
    raw = os.getenv("OPENAI_CONTAINER_EXPIRES_MINUTES", "20")
    try:
        minutes = int(raw)
    except Exception:
        minutes = 20
    if minutes < 1:
        return 1
    if minutes > 20:
        return 20
    return minutes


# =============================================================================
# Workflow Input/Output
# =============================================================================


class WorkflowInput(BaseModel):
    """Input for the workflow."""
    file_paths: list[str] = []  # Paths to PDF/DOCX files
    user_instructions: str = ""  # Additional user instructions


class WorkflowResult(BaseModel):
    """Final output from the workflow."""
    config: NormalizerOutput
    source_questions: list[SourceQuestionItem]
    verified_questions: list[SourceQuestionItem]
    generated_questions: list[NewQuestionItem]
    latex_file: str = ""
    evaluation: Optional[dict[str, Any]] = None


def _normalize_theme_value(theme: str) -> str:
    normalized = (theme or "").strip().lower()
    return normalized if normalized in THEME_ALLOWED_VALUES else "physics_abstract"


def _reconcile_verified_questions(
    extracted_questions: list[SourceQuestionItem],
    verified_questions: list[SourceQuestionItem],
) -> list[SourceQuestionItem]:
    """
    Ensure no multiple-choice source question is dropped by verifier output.
    Open-ended questions are allowed to be excluded from later generation.
    """
    extracted_mc = []
    for q in extracted_questions:
        if q.options:
            q.question_type = "multiple_choice"
        if q.question_type == "multiple_choice":
            q.theme = _normalize_theme_value(q.theme)
            extracted_mc.append(q)
    verified_by_id = {q.source_id: q for q in verified_questions}
    reconciled: list[SourceQuestionItem] = []
    for q in extracted_mc:
        verified_q = verified_by_id.get(q.source_id)
        if verified_q is None:
            fallback = q.model_copy(deep=True)
            for opt in fallback.options:
                if not opt.explanation:
                    opt.explanation = "Not verified; preserved to prevent question loss."
            fallback.has_explicit_answer = bool(any(opt.is_correct for opt in fallback.options))
            fallback.theme = _normalize_theme_value(fallback.theme)
            reconciled.append(fallback)
            continue
        if verified_q.options:
            verified_q.question_type = "multiple_choice"
        verified_q.theme = _normalize_theme_value(verified_q.theme)
        reconciled.append(verified_q)
    return reconciled


def _missing_generation_source_ids(
    source_questions: list[SourceQuestionItem],
    generated_questions: list[NewQuestionItem],
    per_source: int,
) -> list[str]:
    generated_count_by_source: dict[str, int] = {}
    for q in generated_questions:
        generated_count_by_source[q.source_id] = generated_count_by_source.get(q.source_id, 0) + 1
    missing: list[str] = []
    for src in source_questions:
        current = generated_count_by_source.get(src.source_id, 0)
        if current < per_source:
            missing.append(src.source_id)
    return missing


# =============================================================================
# Main Workflow
# =============================================================================


async def run_workflow(workflow_input: WorkflowInput) -> WorkflowResult:
    """
    Run the full isomorphic question generation workflow.
    
    Steps:
        1. Upload files to OpenAI
        2. Normalize user request -> configuration
        3. Extract questions from files (agent uses code interpreter)
        4. Verify answers
        5. Generate isomorphic questions
    
    Args:
        workflow_input: Input containing file paths and instructions
    
    Returns:
        WorkflowResult with all stages' outputs
    """
    with trace("Isomorphic Question Generation Workflow"):
        # Setup output directory
        timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        outputs_dir = PROJECT_ROOT / "outputs" / timestamp
        outputs_dir.mkdir(parents=True, exist_ok=True)
        
        run_config = RunConfig(
            trace_metadata={
                "__trace_source__": "isomorphic-question-generator",
                "timestamp": timestamp,
            }
        )
        agent_outputs: list[dict[str, Any]] = []
        
        # =================================================================
        # Step 0: Upload files to OpenAI
        # =================================================================
        logger.info("Uploading files to OpenAI...")
        file_ids = []
        file_names = []
        for file_path in workflow_input.file_paths:
            path = Path(file_path)
            if path.exists():
                file_id = upload_file_to_openai(path)
                file_ids.append(file_id)
                file_names.append(path.name)
                logger.info("Uploaded: %s -> %s", path.name, file_id)
            else:
                logger.warning("File not found: %s", file_path)
        
        # Create a single shared Code Interpreter container for this workflow
        # so that all agent calls reuse the same environment.
        agents_config = load_agents_config()
        shared_container_id: Optional[str] = None
        if _any_agent_uses_code_interpreter(agents_config):
            if not hasattr(client, "containers"):
                raise RuntimeError(
                    "OpenAI SDK does not support containers; upgrade openai to >=2.0.0"
                )
            container = client.containers.create(
                name=f"isomorphic-workflow-cli-{timestamp}",
                expires_after={"anchor": "last_active_at", "minutes": _container_expires_minutes()},
                file_ids=file_ids,
            )
            shared_container_id = container.id

        # Create agents (use shared container if available)
        agents = create_agents(file_ids=file_ids, container_id=shared_container_id)
        
        # =================================================================
        # Step 1: Normalize User Request
        # =================================================================
        logger.info("STEP 1: Normalizing user request")
        
        normalizer_input = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"""Parse this user request and extract configuration.
                        
Files provided: {', '.join(file_names) if file_names else 'None'}

User instructions:
{workflow_input.user_instructions or 'Create isomorphic questions from the provided files.'}

Defaults (use if not specified):
- new_questions_per_question: 1
- difficulty: "" (keep same as source)
- theme: "" (keep same as source)  
- title: "" (extract from source if available)
""",
                    }
                ],
            }
        ]
        
        normalizer_result = await run_agent(
            agents["normalizer"],
            normalizer_input,
            run_config,
            agent_outputs,
            "user",
            "extracter",
        )
        normalizer_output: NormalizerOutput = normalizer_result.final_output
        logger.info("Normalizer config: %s", normalizer_output.model_dump_json())
        
        # =================================================================
        # Step 2: Extract Questions from Files
        # =================================================================
        logger.info("STEP 2: Extracting questions from files")
        
        # Build file references for the agent
        file_refs = []
        for file_id in file_ids:
            file_refs.append({"type": "input_file", "file_id": file_id})
        
        extracter_input = [
            {
                "role": "user",
                "content": [
                    *file_refs,
                    {
                        "type": "input_text",
                        "text": f"""Extract all questions from the attached file(s).

Use your code interpreter to:
1. Read the PDF/DOCX file(s) - use PyPDF2, pdfplumber, or python-docx
2. Extract all text content
3. Parse and identify individual questions
4. Structure them according to the schema

Files attached: {', '.join(file_names)}

Remember:
- Preserve all mathematical notation (use LaTeX format)
- Extract metadata (title, author, date, course) from headers
- Identify question types (multiple_choice vs open_ended)
- Multiple-choice questions must not be skipped
- For multiple choice, extract all options with their keys (A, B, C, D)
- Assign a theme to each question: life_science | engineering | everyday_activity | physics_abstract
- Set has_explicit_answer=true only if the correct answer is clearly marked
""",
                    },
                ],
            }
        ]
        
        extracter_result = await run_agent(
            agents["extracter"],
            extracter_input,
            run_config,
            agent_outputs,
            "normalizer",
            "verifier",
        )
        extracter_output: ExtracterOutput = extracter_result.final_output
        if file_ids and not extracter_output.questions:
            retry_extract_input = [
                {
                    "role": "user",
                    "content": [
                        *file_refs,
                        {
                            "type": "input_text",
                            "text": f"""The previous extraction returned zero questions.

Re-read every attached file and extract all questions and metadata.
Multiple-choice questions must not be skipped.
Return structured output that includes each question's theme.

Files attached: {', '.join(file_names)}
""",
                        },
                    ],
                }
            ]
            retry_extract_result = await run_agent(
                agents["extracter"],
                retry_extract_input,
                run_config,
                agent_outputs,
                "extracter",
                "verifier",
            )
            extracter_output = retry_extract_result.final_output

        for q in extracter_output.questions:
            if q.options:
                q.question_type = "multiple_choice"
            q.theme = _normalize_theme_value(q.theme)
        if file_ids and not extracter_output.questions:
            raise ValueError("Extractor returned zero questions after retry.")
        logger.info("Extracted %d questions", len(extracter_output.questions))
        
        # =================================================================
        # Step 3: Verify Answers
        # =================================================================
        logger.info("STEP 3: Verifying answers")
        
        verifier_input = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"""Verify the answers for these extracted questions.

Questions to verify:
{extracter_output.model_dump_json(indent=2)}

For each question:
1. If has_explicit_answer=true, keep the marked answer
2. If no answer is marked, solve the problem and mark the correct option
3. Add explanations for each option
4. Do not remove any question. Preserve all multiple-choice questions.
5. Use code interpreter to verify calculations if needed
""",
                    }
                ],
            }
        ]
        
        verifier_result = await run_agent(
            agents["verifier"],
            verifier_input,
            run_config,
            agent_outputs,
            "extracter",
            "generator",
        )
        verifier_output: VerifierOutput = verifier_result.final_output
        verifier_output.questions = _reconcile_verified_questions(
            extracter_output.questions,
            verifier_output.questions,
        )
        logger.info("Verified %d multiple-choice questions", len(verifier_output.questions))
        
        # =================================================================
        # Step 4: Generate Isomorphic Questions
        # =================================================================
        logger.info("STEP 4: Generating isomorphic questions")
        
        generator_input = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"""Generate isomorphic questions based on this configuration and source questions.

Configuration:
{normalizer_output.model_dump_json(indent=2)}

Source Questions:
{verifier_output.model_dump_json(indent=2)}

Requirements:
- Create {normalizer_output.new_questions_per_question} new question(s) per source question
- Each new question must be fully original but test the same concept
- Vary numerical values significantly but keep them physically reasonable
- Create plausible distractors based on common misconceptions
- Include detailed explanations for the correct answer
- Include misconception-based explanations for each distractor
- Set theme on every generated question using the allowed categories
- Use LaTeX for all mathematical expressions
- Use code interpreter to verify your calculations
""",
                    }
                ],
            }
        ]
        
        generator_result = await run_agent(
            agents["generator"],
            generator_input,
            run_config,
            agent_outputs,
            "verifier",
            "latex",
        )
        generator_output: GeneratorOutput = generator_result.final_output
        for q in generator_output.isomorphic_questions:
            q.theme = _normalize_theme_value(q.theme)

        missing_source_ids = _missing_generation_source_ids(
            verifier_output.questions,
            generator_output.isomorphic_questions,
            normalizer_output.new_questions_per_question,
        )
        if missing_source_ids:
            retry_questions = [
                q for q in verifier_output.questions if q.source_id in missing_source_ids
            ]
            retry_input = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"""You omitted required source questions in the prior pass.

Generate only for these missing source IDs: {', '.join(missing_source_ids)}.
Return exactly {normalizer_output.new_questions_per_question} generated question(s) per source_id.

Configuration:
{normalizer_output.model_dump_json(indent=2)}

Source Questions:
{json.dumps({'questions': [q.model_dump() for q in retry_questions]}, indent=2)}
""",
                        }
                    ],
                }
            ]
            retry_result = await run_agent(
                agents["generator"],
                retry_input,
                run_config,
                agent_outputs,
                "generator",
                "generator",
            )
            retry_output: GeneratorOutput = retry_result.final_output
            missing_source_ids_set = set(missing_source_ids)
            for q in retry_output.isomorphic_questions:
                q.theme = _normalize_theme_value(q.theme)
            generator_output.isomorphic_questions.extend(
                q for q in retry_output.isomorphic_questions
                if q.source_id in missing_source_ids_set
            )
            missing_source_ids = _missing_generation_source_ids(
                verifier_output.questions,
                generator_output.isomorphic_questions,
                normalizer_output.new_questions_per_question,
            )
            if missing_source_ids:
                raise ValueError(
                    f"Generator failed to produce required outputs for source_id(s): {', '.join(missing_source_ids)}"
                )

        logger.info("Generated %d new questions", len(generator_output.isomorphic_questions))
        
        # =================================================================
        # Step 5: Format as LaTeX
        # =================================================================
        logger.info("STEP 5: Formatting as LaTeX")
        
        # Prepare questions for LaTeX formatting
        questions_for_latex = []
        for q in generator_output.isomorphic_questions:
            questions_for_latex.append({
                "source_id": q.source_id,
                "stem": q.stem,
                "options": [opt.model_dump() for opt in q.options],
            })
        
        latex_input = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"""Convert these questions to LaTeX format.

Questions:
{json.dumps({"questions": questions_for_latex}, indent=2)}

Create a complete LaTeX document with:
1. A document preamble with necessary packages
2. Custom macros for question formatting (\\QBlock, \\Correct, \\Wrong)
3. All questions formatted using the macros
4. Proper math formatting for all expressions

Title: {normalizer_output.title or "Isomorphic Physics Questions"}
Set filename to "isomorphic_questions.tex"
""",
                    }
                ],
            }
        ]
        
        latex_result = await run_agent(
            agents["latex"],
            latex_input,
            run_config,
            agent_outputs,
            "generator",
            None,
        )
        latex_output: LaTeXOutput = latex_result.final_output
        
        # Save LaTeX file
        latex_filename = latex_output.filename or "isomorphic_questions.tex"
        latex_path = outputs_dir / latex_filename
        latex_path.write_text(latex_output.latex_content, encoding="utf-8")
        logger.info("Created LaTeX file: %s", latex_filename)
        
        # =================================================================
        # Save final results
        # =================================================================
        print("\n" + "=" * 60)
        print("STEP 6: Writing output artifacts")
        print("=" * 60)
        output_stage_start = time.time()

        evaluation_report = run_openai_output_evaluation(
            client=client,
            final_result={
                "source_questions": [q.model_dump() for q in extracter_output.questions],
                "verified_questions": [q.model_dump() for q in verifier_output.questions],
                "generated_questions": [q.model_dump() for q in generator_output.isomorphic_questions],
            },
            outputs_dir=outputs_dir,
            latex_path=latex_path,
        )

        final_result = WorkflowResult(
            config=normalizer_output,
            source_questions=extracter_output.questions,
            verified_questions=verifier_output.questions,
            generated_questions=generator_output.isomorphic_questions,
            latex_file=latex_filename,
            evaluation=evaluation_report,
        )
        
        result_path = outputs_dir / "final_result.json"
        result_path.write_text(final_result.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Results saved to: %s", result_path)
        logger.info("LaTeX file: %s", latex_path)
        output_files = sorted(
            file_path.name for file_path in outputs_dir.iterdir() if file_path.is_file()
        )
        if "agent_outputs.json" not in output_files:
            output_files.append("agent_outputs.json")
            output_files.sort()
        output_elapsed = time.time() - output_stage_start
        agent_outputs.append(
            {
                "agent_name": "Output",
                "execution_time_seconds": output_elapsed,
                "received_from_node": "latex",
                "sent_to_node": "user",
                "input_summary": "Finalize local output files",
                "output": {
                    "output_folder": str(outputs_dir),
                    "files": output_files,
                },
            }
        )

        agent_outputs_path = outputs_dir / "agent_outputs.json"
        agent_outputs_path.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now().isoformat(),
                    "agents": agent_outputs,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        print(f"\n✅ Results saved to: {result_path}")
        print(f"✅ LaTeX file: {latex_path}")
        print(f"✅ Agent outputs saved to: {agent_outputs_path}")
        
        return final_result


# =============================================================================
# CLI Entry Point
# =============================================================================


def main():
    """Command-line entry point."""
    import argparse
    
    parser = argparse.ArgumentParser(
        description="Generate isomorphic physics questions from PDF/DOCX files"
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Path(s) to PDF or DOCX files",
    )
    parser.add_argument(
        "-i", "--instructions",
        default="",
        help="Additional instructions for question generation",
    )
    parser.add_argument(
        "-n", "--num-questions",
        type=int,
        default=1,
        help="Number of isomorphic questions per source question",
    )
    
    args = parser.parse_args()
    
    # If no files provided, use default test file
    if not args.files:
        default_file = QUESTION_DATA_DIR / "PDF" / "Quiz_Compilation.pdf"
        if default_file.exists():
            args.files = [str(default_file)]
            logger.info("Using default file: %s", default_file)
        else:
            logger.error("No files provided and default file not found: %s", default_file)
            raise SystemExit(1)

    # Build instructions
    instructions = args.instructions
    if args.num_questions != 1:
        instructions = f"Generate {args.num_questions} questions per source. " + instructions

    # Run workflow
    workflow_input = WorkflowInput(
        file_paths=args.files,
        user_instructions=instructions,
    )

    try:
        result = asyncio.run(run_workflow(workflow_input))
    except Exception as exc:
        logger.error("Workflow failed: %s", exc)
        raise SystemExit(1) from exc

    # Log summary
    logger.info("WORKFLOW COMPLETE")
    logger.info("Source questions:    %d", len(result.source_questions))
    logger.info("Verified questions:  %d", len(result.verified_questions))
    logger.info("Generated questions: %d", len(result.generated_questions))

    if result.generated_questions:
        for q in result.generated_questions:
            logger.info("[Q%d] (from source %s): %s", q.id, q.source_id, q.stem[:80])


if __name__ == "__main__":
    main()
