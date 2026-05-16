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
try:
    from .artifacts import (
        AGENT_DISPLAY_NAMES,
        write_agent_artifacts,
        write_workflow_metadata,
    )
    from .evaluation import run_openai_output_evaluation
except ImportError:
    from artifacts import (
        AGENT_DISPLAY_NAMES,
        write_agent_artifacts,
        write_workflow_metadata,
    )
    from evaluation import run_openai_output_evaluation
try:
    from .container import SharedWorkflowRuntime, allowed_domains_from_env
    from .schemas import (
        AGENT_OUTPUT_TYPES,
        ExtracterOutput,
        GeneratorOutput,
        LaTeXOutput,
        NormalizerOutput,
        SourceQuestionItem,
        VerifierOutput,
        WorkflowInput,
        WorkflowResult,
    )
    from .helpers import (
        apply_requested_difficulty,
        build_json_message,
        load_agents_config,
        load_prompt,
        merge_extracted_metadata_into_config,
        normalize_question_types,
        upload_file_to_openai,
    )
    from .safety import create_unique_run_dir, ensure_child_path, safe_filename
    from .pipeline_validation import (
        validate_generator_output,
        validate_verifier_output,
    )
except ImportError:
    from container import SharedWorkflowRuntime, allowed_domains_from_env
    from schemas import (
        AGENT_OUTPUT_TYPES,
        ExtracterOutput,
        GeneratorOutput,
        LaTeXOutput,
        NormalizerOutput,
        SourceQuestionItem,
        VerifierOutput,
        WorkflowInput,
        WorkflowResult,
    )
    from helpers import (
        apply_requested_difficulty,
        build_json_message,
        load_agents_config,
        load_prompt,
        merge_extracted_metadata_into_config,
        normalize_question_types,
        upload_file_to_openai,
    )
    from safety import create_unique_run_dir, ensure_child_path, safe_filename
    from pipeline_validation import (
        validate_generator_output,
        validate_verifier_output,
    )

# =============================================================================
# Configuration
# =============================================================================

THIS_DIR = Path(__file__).resolve().parent


BACKEND_ROOT = THIS_DIR.parent if THIS_DIR.name == "src" else THIS_DIR
REPO_ROOT = BACKEND_ROOT.parent
PROJECT_ROOT = BACKEND_ROOT
ENV_PATH = REPO_ROOT / ".env"
DATA_DIR = BACKEND_ROOT / "data"
PROMPTS_DIR = DATA_DIR / "prompts"
AGENTS_CONFIG_PATH = DATA_DIR / "agents.json"
QUESTION_DATA_DIR = REPO_ROOT / "Question Data Files"
if not QUESTION_DATA_DIR.exists():
    QUESTION_DATA_DIR = REPO_ROOT / "archive" / "Question Data Files"


def _style_mismatch_source_ids(
    source_questions: list[SourceQuestionItem],
    generated_questions: list[Any],
    min_ratio: float = 0.5,
    max_ratio: float = 2.0,
    min_source_len: int = 40,
) -> list[str]:
    """Return source_ids whose generated stem length is well outside source length.

    Cheap heuristic for when the generator likely compressed or padded the stem,
    used to decide whether the second-pass style-review LLM call is worth firing.
    Short source stems are skipped to avoid noisy ratios on terse items.
    """
    source_by_id = {q.source_id: q for q in source_questions}
    outliers: list[str] = []
    for generated in generated_questions:
        if getattr(generated, "skipped", False):
            continue
        source = source_by_id.get(getattr(generated, "source_id", None))
        if source is None or source.has_figure:
            continue
        source_len = len((source.source_raw or source.stem or "").strip())
        if source_len < min_source_len:
            continue
        generated_len = len((getattr(generated, "stem", "") or "").strip())
        if generated_len == 0:
            outliers.append(source.source_id)
            continue
        ratio = generated_len / source_len
        if ratio < min_ratio or ratio > max_ratio:
            outliers.append(source.source_id)
    return outliers


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

_cli_openai_client: OpenAI | None = None


def get_cli_openai_client() -> OpenAI:
    """Create the OpenAI client lazily so imports/help do not need credentials."""
    global _cli_openai_client
    if _cli_openai_client is None:
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is required to run the workflow.")
        _cli_openai_client = OpenAI(api_key=api_key)
    return _cli_openai_client



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


def _agent_key_from_display_name(agent_name: str) -> str:
    """Map a human-readable agent name back to its configuration key."""
    normalized_name = agent_name.strip().lower()
    for agent_key, display_name in AGENT_DISPLAY_NAMES.items():
        if normalized_name == display_name.lower():
            return agent_key
    if normalized_name == "latex":
        return "latex"
    return normalized_name.replace(" ", "_")


def _write_agent_artifact_layout(
    *,
    outputs_dir: Path,
    workflow_id: str,
    agent_outputs: list[dict[str, Any]],
    output_folder_label: str,
    source_files: list[str],
) -> Path:
    """Persist agent logs and workflow metadata in the canonical artifact layout."""
    metadata_records: list[dict[str, Any]] = []
    for record in agent_outputs:
        agent_name = str(record.get("agent_name", "Agent"))
        agent_key = _agent_key_from_display_name(agent_name)
        input_payload = record.get("input_messages")
        if input_payload is None:
            input_payload = {"summary": record.get("input_summary", "")}
        output_payload = record.get("output_object")
        if output_payload is None:
            output_payload = record.get("output")
        if output_payload is None:
            output_payload = record.get("output_as_text", "")

        metadata_records.append(
            write_agent_artifacts(
                outputs_dir=outputs_dir,
                workflow_id=workflow_id,
                agent_key=agent_key,
                agent_name=agent_name,
                input_payload=input_payload,
                output_payload=output_payload,
                metadata={
                    "model": record.get("model"),
                    "reasoning_level": record.get("reasoning_level"),
                    "execution_time_seconds": record.get("execution_time_seconds"),
                    "received_from_node": record.get("received_from_node"),
                    "sent_to_node": record.get("sent_to_node"),
                    "agent_received_from": record.get("agent_received_from"),
                    "agent_sent_to": record.get("agent_sent_to"),
                    "input_summary": record.get("input_summary"),
                    "questions_count": record.get("questions_count"),
                    "filename": record.get("filename"),
                    "pdf_filename": record.get("pdf_filename"),
                    "output_folder": record.get("output", {}).get("output_folder")
                    if isinstance(record.get("output"), dict)
                    else None,
                    "files": record.get("output", {}).get("files")
                    if isinstance(record.get("output"), dict)
                    else None,
                },
            )
        )

    return write_workflow_metadata(
        outputs_dir=outputs_dir,
        workflow_id=workflow_id,
        agents=metadata_records,
        output_folder=output_folder_label,
        source_files=source_files,
    )

# =============================================================================
# Agent Definitions
# =============================================================================


def create_agents(
    *,
    file_ids: Optional[list[str]] = None,
    container_id: Optional[str] = None,
    latex_compile_tool: Any | None = None,
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
    agents_config = load_agents_config(AGENTS_CONFIG_PATH, logger)
    
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
        if agent_key == "latex" and latex_compile_tool is not None:
            tools.append(latex_compile_tool)
        
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
            instructions=load_prompt(PROMPTS_DIR, logger, agent_key),
            model=config.get("model", "gpt-5.5"),
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
    """Return True when any configured agent requests the code interpreter."""
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
    client = get_cli_openai_client()
    with trace("Isomorphic Question Generation Workflow"):
        # Setup output directory
        timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        outputs_dir, output_folder_label = create_unique_run_dir(
            PROJECT_ROOT / "outputs",
            f"cli-{timestamp}",
        )
        
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
                file_id = upload_file_to_openai(client, path, logger)
                file_ids.append(file_id)
                file_names.append(path.name)
                logger.info("Uploaded: %s -> %s", path.name, file_id)
            else:
                logger.warning("File not found: %s", file_path)
        
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
        shared_runtime = SharedWorkflowRuntime(
            client=client,
            container_id=shared_container_id,
            output_dir=outputs_dir,
            allowed_domains=allowed_domains_from_env(),
        )
        shared_runtime.bootstrap()

        # Create agents against the single shared workflow container.
        agents = create_agents(
            file_ids=file_ids,
            container_id=shared_container_id,
            latex_compile_tool=shared_runtime.build_compile_latex_tool(),
        )
        
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
- difficulty: "" (leave empty unless the user explicitly requests a difficulty target)
- theme: "" (the extracter will infer source theme if the user does not request one)
- title: "" (the extracter will infer source title if the user does not request one)
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

Normalizer config:
{normalizer_output.model_dump_json(indent=2)}

Use your code interpreter to:
1. Read the PDF/DOCX file(s) - use PyPDF2, pdfplumber, or python-docx
2. Extract all text content
3. Parse and identify individual questions
4. Structure them according to the schema

Files attached: {', '.join(file_names)}

Remember:
- Preserve all mathematical notation (use LaTeX format)
- Determine and populate the top-level title from the source header/title block, or a concise cleaned filename fallback
- Determine and populate the top-level theme from the extracted questions
- Extract metadata (title, author, date, course) from headers
- Identify question types (multiple_choice vs open_ended)
- Multiple-choice questions must not be skipped
- For multiple choice, extract all options with their keys (A, B, C, D)
- Set has_explicit_answer=true only if the correct answer is clearly marked
- Leave difficulty empty unless normalizer_config.difficulty is non-empty
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
            file_diag = ", ".join(
                f"{Path(p).name} ({Path(p).stat().st_size if Path(p).exists() else 'missing'} bytes)"
                for p in workflow_input.file_paths
            )
            logger.warning(
                "Extractor returned zero questions on first pass; retrying. Files: %s",
                file_diag,
            )
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
Determine title and theme from the extracted source information.
Leave difficulty empty unless the user supplied an explicit difficulty target.

Files attached: {', '.join(file_names)}
Normalizer config:
{normalizer_output.model_dump_json(indent=2)}
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
        if file_ids and not extracter_output.questions:
            raise ValueError("Extractor returned zero questions after retry.")
        merge_extracted_metadata_into_config(normalizer_output, extracter_output)
        logger.info(
            "Extractor metadata: title=%s theme=%s",
            extracter_output.title or "",
            extracter_output.theme or "",
        )
        logger.info("Effective config: %s", normalizer_output.model_dump_json())
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
        normalize_question_types(verifier_output.questions)

        verifier_report = validate_verifier_output(extracter_output.questions, verifier_output)
        if not verifier_report.valid:
            repair_ids = set(verifier_report.repair_source_ids)
            repair_questions = [
                q for q in extracter_output.questions if q.source_id in repair_ids
            ]
            if repair_questions:
                repair_payload = {
                    "task": "verify_source_questions_repair",
                    "reason": verifier_report.summary(),
                    "source_questions": {
                        **extracter_output.model_dump(),
                        "questions": [q.model_dump() for q in repair_questions],
                    },
                }
                repair_result = await run_agent(
                    agents["verifier"],
                    build_json_message(repair_payload),
                    run_config,
                    agent_outputs,
                    "verifier",
                    "verifier",
                )
                repair_output: VerifierOutput = repair_result.final_output
                merged_questions = [
                    q for q in verifier_output.questions if q.source_id not in repair_ids
                ] + repair_output.questions
                verifier_output = VerifierOutput(questions=merged_questions)
                normalize_question_types(verifier_output.questions)
                verifier_report = validate_verifier_output(extracter_output.questions, verifier_output)
            if not verifier_report.valid:
                raise ValueError(f"Verifier output failed validation: {verifier_report.summary()}")

        apply_requested_difficulty(verifier_output.questions, normalizer_output.difficulty)

        logger.info("Verified %d multiple-choice questions", len(verifier_output.questions))
        
        # =================================================================
        # Step 4: Generate Isomorphic Questions
        # =================================================================
        logger.info("STEP 4: Generating isomorphic questions")
        generator_requirements = {
            "new_questions_per_source": normalizer_output.new_questions_per_question,
            "fully_original_questions": True,
            "same_underlying_concept": True,
            "vary_numerical_values_significantly": True,
            "physically_reasonable_values": True,
            "plausible_misconception_distractors": True,
            "detailed_correct_answer_explanations": True,
            "explain_each_distractor": True,
            "latex_math": True,
            "use_code_interpreter_for_verification": True,
            "skip_questions_with_figures": True,
            "generate_open_ended_for_open_ended_sources": True,
            "open_ended_questions_have_no_options": True,
            "generate_answers_for_all_open_ended": True,
            "generate_multi_part_for_multi_part_sources": True,
            "preserve_source_style_and_verbosity": True,
            "keep_verbose_open_ended_sources_verbose": True,
            "do_not_compress_narrative_stems_into_short_directives": True,
            "do_not_add_padding_to_concise_sources": True,
            "self_review_style_before_return": True,
        }
        generator_input_payload = {
            "task": "generate_isomorphic_questions",
            "configuration": normalizer_output.model_dump(),
            "source_questions": verifier_output.model_dump(),
            "requirements": generator_requirements,
        }
        generator_input = build_json_message(generator_input_payload)
        
        generator_result = await run_agent(
            agents["generator"],
            generator_input,
            run_config,
            agent_outputs,
            "verifier",
            "latex",
        )
        generator_output: GeneratorOutput = generator_result.final_output

        style_outliers = _style_mismatch_source_ids(
            verifier_output.questions, generator_output.isomorphic_questions
        )
        if style_outliers:
            logger.info(
                "Running style review for %d outlier(s): %s",
                len(style_outliers), ", ".join(style_outliers),
            )
            style_review_input = build_json_message(
                {
                    "task": "review_and_revise_isomorphic_questions_for_style",
                    "configuration": normalizer_output.model_dump(),
                    "source_questions": verifier_output.model_dump(),
                    "draft_isomorphic_questions": generator_output.model_dump(),
                    "requirements": {
                        **generator_requirements,
                        "preserve_ids_and_source_ids": True,
                        "return_complete_final_set": True,
                        "revise_only_when_needed": True,
                        "review_focus": [
                            "Preserve the source question's verbosity and rhetorical style.",
                            "Keep verbose open-ended questions verbose and explanation-oriented.",
                            "Do not compress narrative or multi-sentence source stems into short directives.",
                            "Do not add narrative padding to concise sources.",
                            "Preserve part structure and skip placeholders exactly.",
                        ],
                    },
                }
            )
            style_review_result = await run_agent(
                agents["generator"],
                style_review_input,
                run_config,
                agent_outputs,
                "generator",
                "generator",
            )
            generator_output = style_review_result.final_output
        else:
            logger.info("Skipping style review: all stems within length tolerance")
        apply_requested_difficulty(
            generator_output.isomorphic_questions,
            normalizer_output.difficulty,
        )

        generator_report = validate_generator_output(
            verifier_output.questions,
            generator_output,
            normalizer_output.new_questions_per_question,
        )
        if not generator_report.valid:
            repair_questions = [
                q for q in verifier_output.questions
                if q.source_id in set(generator_report.repair_source_ids)
            ]
            repair_payload = {
                "task": "repair_generator_contract_violations",
                "reason": generator_report.summary(),
                "configuration": normalizer_output.model_dump(),
                "source_questions": {
                    "questions": [q.model_dump() for q in repair_questions]
                },
                "draft_isomorphic_questions": generator_output.model_dump(),
                "requirements": {
                    **generator_requirements,
                    "preserve_ids_and_source_ids": True,
                    "return_complete_final_set": True,
                },
            }
            repair_result = await run_agent(
                agents["generator"],
                build_json_message(repair_payload),
                run_config,
                agent_outputs,
                "generator",
                "generator",
            )
            generator_output = repair_result.final_output
            apply_requested_difficulty(
                generator_output.isomorphic_questions,
                normalizer_output.difficulty,
            )
            generator_report = validate_generator_output(
                verifier_output.questions,
                generator_output,
                normalizer_output.new_questions_per_question,
            )
            if not generator_report.valid:
                raise ValueError(f"Generator output failed validation: {generator_report.summary()}")

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
                "question_type": q.question_type,
                "skipped": q.skipped,
                "skip_reason": q.skip_reason,
                "answer": q.answer,
                "parts": [part.model_dump() for part in q.parts],
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
Theme: {normalizer_output.theme or "physics_abstract"}
Source title: {extracter_output.title or ""}
Course: {extracter_output.course or ""}
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
        latex_filename = safe_filename(
            latex_output.filename or "isomorphic_questions.tex",
            fallback="isomorphic_questions.tex",
            allowed_suffixes={".tex"},
        )
        latex_path = ensure_child_path(outputs_dir, latex_filename)
        latex_path.write_text(latex_output.latex_content, encoding="utf-8")
        pdf_filename = safe_filename(
            latex_output.pdf_filename or "isomorphic_questions.pdf",
            fallback="isomorphic_questions.pdf",
            allowed_suffixes={".pdf"},
        )
        pdf_path = ensure_child_path(outputs_dir, pdf_filename)
        if not pdf_path.exists():
            raise RuntimeError(
                "Latexifier did not produce the compiled PDF artifact in the shared container."
            )
        logger.info("Created LaTeX file: %s", latex_filename)
        logger.info("Compiled PDF file: %s", pdf_filename)
        
        # =================================================================
        # Save final results
        # =================================================================
        print("\n" + "=" * 60)
        print("STEP 6: Writing output artifacts")
        print("=" * 60)
        evaluation_stage_start = time.time()

        evaluation_report = await asyncio.to_thread(
            run_openai_output_evaluation,
            client=client,
            final_result={
                "source_questions": [q.model_dump() for q in extracter_output.questions],
                "verified_questions": [q.model_dump() for q in verifier_output.questions],
                "generated_questions": [q.model_dump() for q in generator_output.isomorphic_questions],
            },
            outputs_dir=outputs_dir,
            latex_path=latex_path,
            source_files=workflow_input.file_paths,
            contract_report={
                "status": "passed" if generator_report.valid else "failed",
                **generator_report.model_dump(),
            },
        )
        evaluation_elapsed = time.time() - evaluation_stage_start
        agent_outputs.append(
            {
                "agent_name": "Evaluation",
                "execution_time_seconds": evaluation_elapsed,
                "received_from_node": "latex",
                "sent_to_node": "output",
                "input_summary": "Evaluate generated output artifacts",
                "output": evaluation_report,
            }
        )

        output_stage_start = time.time()

        final_result = WorkflowResult(
            config=normalizer_output,
            source_questions=extracter_output.questions,
            verified_questions=verifier_output.questions,
            generated_questions=generator_output.isomorphic_questions,
            latex_file=latex_filename,
            pdf_file=pdf_filename,
            evaluation=evaluation_report,
        )
        
        result_path = outputs_dir / "final_result.json"
        result_path.write_text(final_result.model_dump_json(indent=2), encoding="utf-8")
        logger.info("Results saved to: %s", result_path)
        logger.info("LaTeX file: %s", latex_path)
        logger.info("PDF file: %s", pdf_path)
        # File listing will be populated after artifacts are written; placeholder
        # for now so the Output agent record gets a value even if rglob misses
        # late writes (the post-write pass below overwrites this in place).
        output_files: list[str] = []
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

        agent_metadata_path = _write_agent_artifact_layout(
            outputs_dir=outputs_dir,
            workflow_id=f"cli-{timestamp}",
            agent_outputs=agent_outputs,
            output_folder_label=output_folder_label,
            source_files=workflow_input.file_paths,
        )

        output_files.extend(
            sorted(
                str(p.relative_to(outputs_dir)).replace(os.sep, "/")
                for p in outputs_dir.rglob("*")
                if p.is_file()
            )
        )

        print(f"\n✅ Results saved to: {result_path}")
        print(f"✅ LaTeX file: {latex_path}")
        print(f"✅ PDF file: {pdf_path}")
        print(f"✅ Agent metadata saved to: {agent_metadata_path}")
        
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
