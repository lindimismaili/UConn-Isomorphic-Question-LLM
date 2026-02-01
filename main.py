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

# =============================================================================
# Configuration
# =============================================================================

THIS_DIR = Path(__file__).resolve().parent


def _detect_repo_root(start: Path) -> Path:
    candidates = [start, start.parent]
    for candidate in candidates:
        if (candidate / "Prompts").is_dir():
            return candidate
    return start


REPO_ROOT = _detect_repo_root(THIS_DIR)
PROJECT_ROOT = REPO_ROOT
ENV_PATH = REPO_ROOT / ".env"
PROMPTS_DIR = REPO_ROOT / "Prompts"
QUESTION_DATA_DIR = REPO_ROOT / "Question Data Files"


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


def load_prompt(name: str) -> str:
    """Load a prompt from the Prompts directory."""
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


def create_vector_store(name: str, file_ids: list[str]) -> str:
    """Create a vector store with the given files for file search."""
    vector_store = client.vector_stores.create(name=name, file_ids=file_ids)
    logger.info("Created vector store %s -> %s", name, vector_store.id)
    return vector_store.id


# =============================================================================
# Agent Runner Utility
# =============================================================================


async def run_agent(
    agent: Agent,
    inputs: list[dict[str, Any]],
    run_config: RunConfig,
    outputs_dir: Path,
    received_from: str,
    sent_to: Optional[str],
    output_index: int,
) -> Any:
    """
    Run an agent and log the results.
    
    Args:
        agent: The agent to run
        inputs: Input messages for the agent
        run_config: Configuration for the run
        outputs_dir: Directory to save output logs
        received_from: Name of the previous agent in workflow
        sent_to: Name of the next agent in workflow
        output_index: Index for output file naming
    
    Returns:
        The agent run result
    """
    print(f"🚀 Running agent: {agent.name}")
    start_time = time.time()
    
    result = await Runner.run(agent, input=inputs, run_config=run_config)
    
    end_time = time.time()
    elapsed = end_time - start_time
    
    # Extract reasoning level if available
    reasoning_level = None
    if agent.model_settings and agent.model_settings.reasoning:
        reasoning_level = agent.model_settings.reasoning.effort
    
    # Build log payload
    # Format input/output labels
    input_label = "USER INPUT" if received_from == "user" else f"Input From Agent {received_from.capitalize()}"
    output_label = "END USER" if sent_to is None else f"Output To Agent {sent_to.capitalize()}"
    
    log_payload = {
        "agent_name": agent.name,
        "model": agent.model,
        "reasoning_level": reasoning_level,
        "execution_time_seconds": elapsed,
        "input_summary": f"{len(inputs)} message(s)",
        "output_as_text": result.final_output.model_dump_json() if hasattr(result.final_output, "model_dump_json") else str(result.final_output),
        "agent_received_from": input_label,
        "agent_sent_to": output_label,
    }
    
    # Save log to file
    safe_name = agent.name.replace(" ", "_").lower()
    file_name = f"{output_index}_{safe_name}.json"
    log_path = outputs_dir / file_name
    log_path.write_text(json.dumps(log_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    
    print(f"✅ Completed: {agent.name} ({elapsed:.2f}s)")
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
    topic_tags: list[str]


class NewQuestionItem(BaseModel):
    """A newly generated isomorphic question."""
    id: int
    source_id: str
    stem: str
    options: list[OptionItem]
    difficulty: str
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


def create_agents(file_ids: list[str] = None) -> dict[str, Agent]:
    """
    Create all agents with their tools configured.
    
    Args:
        file_ids: List of uploaded file IDs for code interpreter access
    
    Returns:
        Dictionary of agent name -> Agent instance
    """
    file_ids = file_ids or []
    
    # Code interpreter tool with file access
    code_interpreter = CodeInterpreterTool(
        tool_config={
            "type": "code_interpreter",
            "container": {
                "type": "auto",
                "file_ids": file_ids,
            },
        }
    )
    
    # Normalizer: Parses user request into configuration
    normalizer = Agent(
        name="Normalizer",
        instructions=load_prompt("normalizer"),
        model="gpt-5.2",
        output_type=NormalizerOutput,
        model_settings=ModelSettings(
            store=True,
            reasoning=Reasoning(effort="none", summary="auto"),
        ),
    )
    
    # Extracter: Reads files and extracts questions using code interpreter
    extracter = Agent(
        name="Extracter",
        instructions=load_prompt("extracter"),
        model="gpt-5.2",
        tools=[code_interpreter],
        output_type=ExtracterOutput,
        model_settings=ModelSettings(
            store=True,
            reasoning=Reasoning(effort="low", summary="auto"),
        ),
    )
    
    # Verifier: Validates answers and adds explanations
    verifier = Agent(
        name="Verifier",
        instructions=load_prompt("verifier"),
        model="gpt-5.2",
        tools=[code_interpreter],
        output_type=VerifierOutput,
        model_settings=ModelSettings(
            store=True,
            reasoning=Reasoning(effort="low", summary="auto"),
        ),
    )
    
    # Generator: Creates isomorphic questions
    generator = Agent(
        name="Generator",
        instructions=load_prompt("generator"),
        model="gpt-5.2",
        tools=[code_interpreter],
        output_type=GeneratorOutput,
        model_settings=ModelSettings(
            store=True,
            reasoning=Reasoning(effort="medium", summary="auto"),
        ),
    )
    
    # LaTeX Formatter: Converts questions to LaTeX
    latex_formatter = Agent(
        name="LaTeX Formatter",
        instructions=load_prompt("latex"),
        model="gpt-5.2",
        output_type=LaTeXOutput,
        model_settings=ModelSettings(
            store=True,
            reasoning=Reasoning(effort="medium", summary="auto"),
        ),
    )
    
    return {
        "normalizer": normalizer,
        "extracter": extracter,
        "verifier": verifier,
        "generator": generator,
        "latex": latex_formatter,
    }


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
        outputs_dir = PROJECT_ROOT / "Outputs" / timestamp
        outputs_dir.mkdir(parents=True, exist_ok=True)
        
        run_config = RunConfig(
            trace_metadata={
                "__trace_source__": "isomorphic-question-generator",
                "timestamp": timestamp,
            }
        )
        
        # =================================================================
        # Step 0: Upload files to OpenAI
        # =================================================================
        print("\n📁 Uploading files to OpenAI...")
        file_ids = []
        file_names = []
        for file_path in workflow_input.file_paths:
            path = Path(file_path)
            if path.exists():
                file_id = upload_file_to_openai(path)
                file_ids.append(file_id)
                file_names.append(path.name)
                print(f"   ✓ {path.name} -> {file_id}")
            else:
                print(f"   ⚠ File not found: {file_path}")
        
        # Create agents with file access
        agents = create_agents(file_ids)
        
        # =================================================================
        # Step 1: Normalize User Request
        # =================================================================
        print("\n" + "=" * 60)
        print("STEP 1: Normalizing user request")
        print("=" * 60)
        
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
            outputs_dir,
            "user",
            "extracter",
            1,
        )
        normalizer_output: NormalizerOutput = normalizer_result.final_output
        print(f"   Config: {normalizer_output.model_dump_json(indent=2)}")
        
        # =================================================================
        # Step 2: Extract Questions from Files
        # =================================================================
        print("\n" + "=" * 60)
        print("STEP 2: Extracting questions from files")
        print("=" * 60)
        
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
- For multiple choice, extract all options with their keys (A, B, C, D)
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
            outputs_dir,
            "normalizer",
            "verifier",
            2,
        )
        extracter_output: ExtracterOutput = extracter_result.final_output
        print(f"   Extracted {len(extracter_output.questions)} questions")
        
        # =================================================================
        # Step 3: Verify Answers
        # =================================================================
        print("\n" + "=" * 60)
        print("STEP 3: Verifying answers")
        print("=" * 60)
        
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
4. Remove any questions that are not multiple choice
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
            outputs_dir,
            "extracter",
            "generator",
            3,
        )
        verifier_output: VerifierOutput = verifier_result.final_output
        print(f"   Verified {len(verifier_output.questions)} questions")
        
        # =================================================================
        # Step 4: Generate Isomorphic Questions
        # =================================================================
        print("\n" + "=" * 60)
        print("STEP 4: Generating isomorphic questions")
        print("=" * 60)
        
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
            outputs_dir,
            "verifier",
            "latex",
            4,
        )
        generator_output: GeneratorOutput = generator_result.final_output
        print(f"   Generated {len(generator_output.isomorphic_questions)} new questions")
        
        # =================================================================
        # Step 5: Format as LaTeX
        # =================================================================
        print("\n" + "=" * 60)
        print("STEP 5: Formatting as LaTeX")
        print("=" * 60)
        
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
            outputs_dir,
            "generator",
            None,
            5,
        )
        latex_output: LaTeXOutput = latex_result.final_output
        
        # Save LaTeX file
        latex_filename = latex_output.filename or "isomorphic_questions.tex"
        latex_path = outputs_dir / latex_filename
        latex_path.write_text(latex_output.latex_content, encoding="utf-8")
        print(f"   Created {latex_filename}")
        
        # =================================================================
        # Save final results
        # =================================================================
        final_result = WorkflowResult(
            config=normalizer_output,
            source_questions=extracter_output.questions,
            verified_questions=verifier_output.questions,
            generated_questions=generator_output.isomorphic_questions,
            latex_file=latex_filename,
        )
        
        result_path = outputs_dir / "final_result.json"
        result_path.write_text(final_result.model_dump_json(indent=2), encoding="utf-8")
        print(f"\n✅ Results saved to: {result_path}")
        print(f"✅ LaTeX file: {latex_path}")
        
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
            print(f"Using default file: {default_file}")
        else:
            print("Error: No files provided and default file not found.")
            print(f"Expected: {default_file}")
            return
    
    # Build instructions
    instructions = args.instructions
    if args.num_questions != 1:
        instructions = f"Generate {args.num_questions} questions per source. " + instructions
    
    # Run workflow
    workflow_input = WorkflowInput(
        file_paths=args.files,
        user_instructions=instructions,
    )
    
    result = asyncio.run(run_workflow(workflow_input))
    
    # Print summary
    print("\n" + "=" * 60)
    print("WORKFLOW COMPLETE")
    print("=" * 60)
    print(f"Source questions: {len(result.source_questions)}")
    print(f"Verified questions: {len(result.verified_questions)}")
    print(f"Generated questions: {len(result.generated_questions)}")
    
    # Print generated questions
    if result.generated_questions:
        print("\n--- Generated Questions ---")
        for q in result.generated_questions:
            print(f"\n[Q{q.id}] (from source {q.source_id})")
            print(f"   {q.stem}")
            for opt in q.options:
                marker = "✓" if opt.is_correct else " "
                print(f"   [{marker}] {opt.key}: {opt.text}")


if __name__ == "__main__":
    main()
