"""
FastAPI Server for Isomorphic Question Generator

Provides REST API endpoints for:
- File upload
- Workflow execution with SSE streaming
- File downloads
- Static file serving for the frontend
"""

import asyncio
import json
import os
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

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

# =============================================================================
# Configuration
# =============================================================================

THIS_DIR = Path(__file__).resolve().parent


def _detect_repo_root(start: Path) -> Path:
    """
    Detect the repo root for this project.

    When server.py is in src/, the repo root is the parent directory.
    We detect this by looking for index.html (frontend) and prompts/ folder.
    """
    # If we're in src/, go up one level
    if start.name == "src":
        candidate = start.parent
    else:
        candidate = start
    
    # Verify we found the repo root by checking for key files/folders
    if (candidate / "index.html").is_file() or (candidate / "prompts").is_dir():
        return candidate
    
    return start.parent  # Fallback: assume we're in src/


REPO_ROOT = _detect_repo_root(THIS_DIR)
PROJECT_ROOT = REPO_ROOT
STATIC_DIR = REPO_ROOT  # Frontend files are in repo root (index.html, etc.)
ENV_PATH = REPO_ROOT / ".env"
PROMPTS_DIR = REPO_ROOT / "prompts"
OUTPUTS_DIR = REPO_ROOT / "outputs"
UPLOADS_DIR = REPO_ROOT / "uploads"

# Create directories if they don't exist
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)


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

# Initialize OpenAI client
client = OpenAI()

# Initialize FastAPI app
app = FastAPI(
    title="Isomorphic Question Generator API",
    description="Generate isomorphic physics questions using AI agents",
    version="1.0.0",
)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Health Check Endpoint
# =============================================================================


@app.get("/api/health")
async def health_check():
    """Health check endpoint for debugging connectivity issues."""
    return {
        "status": "healthy",
        "uploads_dir": str(UPLOADS_DIR),
        "uploads_dir_exists": UPLOADS_DIR.exists(),
        "uploads_dir_writable": os.access(UPLOADS_DIR, os.W_OK),
        "outputs_dir": str(OUTPUTS_DIR),
        "outputs_dir_exists": OUTPUTS_DIR.exists(),
        "outputs_dir_writable": os.access(OUTPUTS_DIR, os.W_OK),
    }


# =============================================================================
# Helper Functions
# =============================================================================


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


def upload_file_to_openai(file_path: Path) -> str:
    """Upload a file to OpenAI and return the file ID."""
    with open(file_path, "rb") as f:
        response = client.files.create(file=f, purpose="assistants")
    logger.info("Uploaded file %s -> %s", file_path.name, response.id)
    return response.id


# =============================================================================
# Pydantic Models
# =============================================================================


class OptionItem(BaseModel):
    key: str
    text: str
    is_correct: bool
    explanation: str


class SourceQuestionItem(BaseModel):
    source_id: str
    source_raw: str
    stem: str
    options: list[OptionItem]
    has_explicit_answer: bool
    difficulty: str
    question_type: str
    topic_tags: list[str]


class NewQuestionItem(BaseModel):
    id: int
    source_id: str
    stem: str
    options: list[OptionItem]
    difficulty: str
    topic_tags: list[str]
    generation_notes: str


class NormalizerOutput(BaseModel):
    new_questions_per_question: int = 1
    difficulty: str = ""
    theme: str = ""
    title: str = ""
    notes: str = ""


class ExtracterOutput(BaseModel):
    filename: str = ""
    title: str = ""
    author: str = ""
    date: str = ""
    course: str = ""
    questions: list[SourceQuestionItem]


class VerifierOutput(BaseModel):
    questions: list[SourceQuestionItem]


class GeneratorOutput(BaseModel):
    isomorphic_questions: list[NewQuestionItem]


class LaTeXOutput(BaseModel):
    latex_content: str
    filename: str


class WorkflowResult(BaseModel):
    config: NormalizerOutput
    source_questions: list[SourceQuestionItem]
    verified_questions: list[SourceQuestionItem]
    generated_questions: list[NewQuestionItem]
    latex_file: str = ""


# =============================================================================
# In-Memory Storage for Workflows
# =============================================================================

workflows: dict[str, dict] = {}


# =============================================================================
# Agent Creation
# =============================================================================


def create_agents(file_ids: list[str] = None) -> dict[str, Agent]:
    """Create all agents with their tools configured."""
    file_ids = file_ids or []
    
    code_interpreter = CodeInterpreterTool(
        tool_config={
            "type": "code_interpreter",
            "container": {
                "type": "auto",
                "file_ids": file_ids,
            },
        }
    )
    
    normalizer = Agent(
        name="Normalizer",
        instructions=load_prompt("normalizer"),
        model="gpt-4o",
        output_type=NormalizerOutput,
        model_settings=ModelSettings(store=True),
    )
    
    extracter = Agent(
        name="Extracter",
        instructions=load_prompt("extracter"),
        model="gpt-4o",
        tools=[code_interpreter],
        output_type=ExtracterOutput,
        model_settings=ModelSettings(store=True),
    )
    
    verifier = Agent(
        name="Verifier",
        instructions=load_prompt("verifier"),
        model="gpt-4o",
        tools=[code_interpreter],
        output_type=VerifierOutput,
        model_settings=ModelSettings(store=True),
    )
    
    generator = Agent(
        name="Generator",
        instructions=load_prompt("generator"),
        model="gpt-4o",
        tools=[code_interpreter],
        output_type=GeneratorOutput,
        model_settings=ModelSettings(store=True),
    )
    
    latex_formatter = Agent(
        name="LaTeX Formatter",
        instructions=load_prompt("latex"),
        model="gpt-4o",
        output_type=LaTeXOutput,
        model_settings=ModelSettings(store=True),
    )
    
    return {
        "normalizer": normalizer,
        "extracter": extracter,
        "verifier": verifier,
        "generator": generator,
        "latex": latex_formatter,
    }


# =============================================================================
# API Endpoints
# =============================================================================


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """Upload files for processing."""
    logger.info("Upload request received with %d file(s)", len(files))
    uploaded_files = []
    
    try:
        # Create a session directory for uploads
        session_id = str(uuid.uuid4())[:8]
        session_dir = UPLOADS_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Created session directory: %s", session_dir)
        
        for file in files:
            if file.filename:
                # Sanitize filename to prevent path traversal
                safe_filename = Path(file.filename).name
                file_path = session_dir / safe_filename
                logger.info("Saving file: %s", safe_filename)
                
                try:
                    content = await file.read()
                    with open(file_path, "wb") as f:
                        f.write(content)
                    uploaded_files.append({
                        "name": safe_filename,
                        "path": str(file_path),
                        "size": len(content),
                    })
                    logger.info("Saved file %s (%d bytes)", safe_filename, len(content))
                except Exception as file_error:
                    logger.error("Failed to save file %s: %s", safe_filename, file_error)
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to save file {safe_filename}: {str(file_error)}"
                    )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Upload error: %s", e)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")
    
    return {
        "success": True,
        "sessionId": session_id,
        "files": uploaded_files,
    }


@app.get("/api/sliders")
async def get_sliders():
    """Get the slider configuration for the frontend."""
    sliders_path = PROMPTS_DIR / "sliders.json"
    if not sliders_path.exists():
        raise HTTPException(status_code=404, detail="Sliders configuration not found")
    
    try:
        return json.loads(sliders_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load sliders: {str(e)}")


@app.get("/api/prompts/{prompt_name}")
async def get_prompt(prompt_name: str):
    """Get an agent prompt file content."""
    # Validate prompt name to prevent path traversal
    allowed_prompts = ["normalizer", "extracter", "verifier", "generator", "latex", "python"]
    if prompt_name not in allowed_prompts:
        raise HTTPException(status_code=404, detail="Prompt not found")
    
    prompt_path = PROMPTS_DIR / f"{prompt_name}.txt"
    if not prompt_path.exists():
        raise HTTPException(status_code=404, detail="Prompt file not found")
    
    try:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(prompt_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load prompt: {str(e)}")


@app.get("/api/workflow/{workflow_id}/status")
async def get_workflow_status(workflow_id: str):
    """Get the current status of a workflow."""
    if workflow_id not in workflows:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    workflow = workflows[workflow_id]
    return {
        "workflowId": workflow_id,
        "status": workflow.get("status", "unknown"),
        "agents": workflow.get("agents", {}),
        "outputFolder": workflow.get("outputFolder", ""),
    }


@app.get("/api/workflow/{workflow_id}/result")
async def get_workflow_result(workflow_id: str):
    """Get the final result of a workflow."""
    if workflow_id not in workflows:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    workflow = workflows[workflow_id]
    if workflow.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Workflow not yet completed")
    
    return {
        "workflowId": workflow_id,
        "result": workflow.get("result"),
        "files": workflow.get("files", []),
        "outputFolder": workflow.get("outputFolder", ""),
    }


@app.get("/api/download/{workflow_id}/{filename}")
async def download_file(workflow_id: str, filename: str):
    """Download a file from a completed workflow."""
    if workflow_id not in workflows:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    workflow = workflows[workflow_id]
    output_folder = workflow.get("outputFolder")
    if not output_folder:
        raise HTTPException(status_code=404, detail="Output folder not found")
    
    file_path = OUTPUTS_DIR / output_folder / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="application/octet-stream",
    )


@app.post("/api/workflow/start")
async def start_workflow(
    prompt: str = Form(""),
    sessionId: str = Form(""),
    filePaths: str = Form("[]"),
):
    """Start a new workflow and return a stream of status updates."""
    workflow_id = str(uuid.uuid4())[:8]
    
    # Parse file paths
    try:
        file_paths = json.loads(filePaths) if filePaths else []
    except json.JSONDecodeError:
        file_paths = []
    
    # If sessionId provided, get files from upload directory
    if sessionId:
        session_dir = UPLOADS_DIR / sessionId
        if session_dir.exists():
            file_paths = [str(f) for f in session_dir.iterdir() if f.is_file()]
    
    # Initialize workflow state
    workflows[workflow_id] = {
        "status": "starting",
        "agents": {
            "normalizer": {"status": "inactive", "data": {}},
            "extracter": {"status": "inactive", "data": {}},
            "verifier": {"status": "inactive", "data": {}},
            "generator": {"status": "inactive", "data": {}},
            "latex": {"status": "inactive", "data": {}},
        },
        "files": [],
        "outputFolder": "",
        "result": None,
    }
    
    async def generate_events():
        """Generate SSE events for workflow progress."""
        try:
            yield f"data: {json.dumps({'event': 'started', 'workflowId': workflow_id})}\n\n"
            
            # Run the workflow
            result = await run_workflow_with_events(
                workflow_id, prompt, file_paths, generate_events
            )
            
            yield f"data: {json.dumps({'event': 'completed', 'workflowId': workflow_id})}\n\n"
            
        except Exception as e:
            logger.error("Workflow error: %s", e)
            workflows[workflow_id]["status"] = "errored"
            yield f"data: {json.dumps({'event': 'error', 'message': str(e)})}\n\n"
    
    # Start workflow in background
    asyncio.create_task(run_workflow_async(workflow_id, prompt, file_paths))
    
    return {"success": True, "workflowId": workflow_id}


@app.get("/api/workflow/{workflow_id}/stream")
async def stream_workflow(workflow_id: str):
    """Stream workflow status updates using SSE."""
    if workflow_id not in workflows:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    async def generate_events():
        """Generate SSE events for workflow progress."""
        last_status = None
        while True:
            workflow = workflows.get(workflow_id)
            if not workflow:
                break
            
            # Send current status
            current_status = json.dumps({
                "status": workflow.get("status"),
                "agents": workflow.get("agents"),
                "outputFolder": workflow.get("outputFolder"),
            })
            
            if current_status != last_status:
                yield f"data: {current_status}\n\n"
                last_status = current_status
            
            # Check if workflow is done
            if workflow.get("status") in ["completed", "errored"]:
                break
            
            await asyncio.sleep(0.5)
    
    return StreamingResponse(
        generate_events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


async def run_workflow_async(workflow_id: str, prompt: str, file_paths: list[str]):
    """Run the workflow asynchronously."""
    workflow = workflows[workflow_id]
    
    try:
        with trace("Isomorphic Question Generation Workflow"):
            # Setup output directory
            timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
            outputs_dir = OUTPUTS_DIR / timestamp
            outputs_dir.mkdir(parents=True, exist_ok=True)
            workflow["outputFolder"] = timestamp
            workflow["status"] = "running"
            
            run_config = RunConfig(
                trace_metadata={
                    "__trace_source__": "isomorphic-question-generator",
                    "timestamp": timestamp,
                }
            )
            
            # Upload files to OpenAI
            file_ids = []
            file_names = []
            for file_path in file_paths:
                path = Path(file_path)
                if path.exists():
                    file_id = upload_file_to_openai(path)
                    file_ids.append(file_id)
                    file_names.append(path.name)
            
            # Create agents
            agents = create_agents(file_ids)
            
            # =================================================================
            # Step 1: Normalizer
            # =================================================================
            workflow["agents"]["normalizer"]["status"] = "thinking"
            workflow["agents"]["normalizer"]["data"]["startTime"] = datetime.now().isoformat()
            
            normalizer_input = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"""Parse this user request and extract configuration.
                            
Files provided: {', '.join(file_names) if file_names else 'None'}

User instructions:
{prompt or 'Create isomorphic questions from the provided files.'}

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
            
            start_time = time.time()
            normalizer_result = await Runner.run(agents["normalizer"], input=normalizer_input, run_config=run_config)
            elapsed = time.time() - start_time
            normalizer_output: NormalizerOutput = normalizer_result.final_output
            
            workflow["agents"]["normalizer"]["status"] = "completed"
            workflow["agents"]["normalizer"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["normalizer"]["data"]["executionTime"] = elapsed
            workflow["agents"]["normalizer"]["data"]["output"] = normalizer_output.model_dump_json()
            
            # Save normalizer output
            (outputs_dir / "1_normalizer.json").write_text(
                json.dumps({
                    "agent_name": "Normalizer",
                    "execution_time_seconds": elapsed,
                    "output": normalizer_output.model_dump(),
                }, indent=2),
                encoding="utf-8"
            )
            
            # =================================================================
            # Step 2: Extracter
            # =================================================================
            workflow["agents"]["extracter"]["status"] = "thinking"
            workflow["agents"]["extracter"]["data"]["startTime"] = datetime.now().isoformat()
            
            file_refs = [{"type": "input_file", "file_id": fid} for fid in file_ids]
            
            extracter_input = [
                {
                    "role": "user",
                    "content": [
                        *file_refs,
                        {
                            "type": "input_text",
                            "text": f"""Extract all questions from the attached file(s).

Use your code interpreter to:
1. Read the PDF/DOCX file(s)
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
            
            start_time = time.time()
            extracter_result = await Runner.run(agents["extracter"], input=extracter_input, run_config=run_config)
            elapsed = time.time() - start_time
            extracter_output: ExtracterOutput = extracter_result.final_output
            
            workflow["agents"]["extracter"]["status"] = "completed"
            workflow["agents"]["extracter"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["extracter"]["data"]["executionTime"] = elapsed
            workflow["agents"]["extracter"]["data"]["output"] = f"Extracted {len(extracter_output.questions)} questions"
            
            # Save extracter output
            (outputs_dir / "2_extracter.json").write_text(
                json.dumps({
                    "agent_name": "Extracter",
                    "execution_time_seconds": elapsed,
                    "questions_count": len(extracter_output.questions),
                    "output": extracter_output.model_dump(),
                }, indent=2),
                encoding="utf-8"
            )
            
            # =================================================================
            # Step 3: Verifier
            # =================================================================
            workflow["agents"]["verifier"]["status"] = "thinking"
            workflow["agents"]["verifier"]["data"]["startTime"] = datetime.now().isoformat()
            
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
            
            start_time = time.time()
            verifier_result = await Runner.run(agents["verifier"], input=verifier_input, run_config=run_config)
            elapsed = time.time() - start_time
            verifier_output: VerifierOutput = verifier_result.final_output
            
            workflow["agents"]["verifier"]["status"] = "completed"
            workflow["agents"]["verifier"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["verifier"]["data"]["executionTime"] = elapsed
            workflow["agents"]["verifier"]["data"]["output"] = f"Verified {len(verifier_output.questions)} questions"
            
            # Save verifier output
            (outputs_dir / "3_verifier.json").write_text(
                json.dumps({
                    "agent_name": "Verifier",
                    "execution_time_seconds": elapsed,
                    "questions_count": len(verifier_output.questions),
                    "output": verifier_output.model_dump(),
                }, indent=2),
                encoding="utf-8"
            )
            
            # =================================================================
            # Step 4: Generator
            # =================================================================
            workflow["agents"]["generator"]["status"] = "thinking"
            workflow["agents"]["generator"]["data"]["startTime"] = datetime.now().isoformat()
            
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
            
            start_time = time.time()
            generator_result = await Runner.run(agents["generator"], input=generator_input, run_config=run_config)
            elapsed = time.time() - start_time
            generator_output: GeneratorOutput = generator_result.final_output
            
            workflow["agents"]["generator"]["status"] = "completed"
            workflow["agents"]["generator"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["generator"]["data"]["executionTime"] = elapsed
            workflow["agents"]["generator"]["data"]["output"] = f"Generated {len(generator_output.isomorphic_questions)} questions"
            
            # Save generator output
            (outputs_dir / "4_generator.json").write_text(
                json.dumps({
                    "agent_name": "Generator",
                    "execution_time_seconds": elapsed,
                    "questions_count": len(generator_output.isomorphic_questions),
                    "output": generator_output.model_dump(),
                }, indent=2),
                encoding="utf-8"
            )
            
            # =================================================================
            # Step 5: LaTeX Formatter
            # =================================================================
            workflow["agents"]["latex"]["status"] = "thinking"
            workflow["agents"]["latex"]["data"]["startTime"] = datetime.now().isoformat()
            
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

Set filename to "isomorphic_questions.tex"
""",
                        }
                    ],
                }
            ]
            
            start_time = time.time()
            latex_result = await Runner.run(agents["latex"], input=latex_input, run_config=run_config)
            elapsed = time.time() - start_time
            latex_output: LaTeXOutput = latex_result.final_output
            
            workflow["agents"]["latex"]["status"] = "completed"
            workflow["agents"]["latex"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["latex"]["data"]["executionTime"] = elapsed
            workflow["agents"]["latex"]["data"]["output"] = f"Created {latex_output.filename}"
            
            # Save LaTeX file
            latex_filename = latex_output.filename or "isomorphic_questions.tex"
            latex_path = outputs_dir / latex_filename
            latex_path.write_text(latex_output.latex_content, encoding="utf-8")
            
            # Save LaTeX agent output
            (outputs_dir / "5_latex.json").write_text(
                json.dumps({
                    "agent_name": "LaTeX Formatter",
                    "execution_time_seconds": elapsed,
                    "filename": latex_filename,
                }, indent=2),
                encoding="utf-8"
            )
            
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
            
            # Update workflow with files
            workflow["files"] = [
                {"name": "final_result.json", "size": result_path.stat().st_size},
                {"name": latex_filename, "size": latex_path.stat().st_size},
                {"name": "1_normalizer.json", "size": (outputs_dir / "1_normalizer.json").stat().st_size},
                {"name": "2_extracter.json", "size": (outputs_dir / "2_extracter.json").stat().st_size},
                {"name": "3_verifier.json", "size": (outputs_dir / "3_verifier.json").stat().st_size},
                {"name": "4_generator.json", "size": (outputs_dir / "4_generator.json").stat().st_size},
                {"name": "5_latex.json", "size": (outputs_dir / "5_latex.json").stat().st_size},
            ]
            workflow["result"] = final_result.model_dump()
            workflow["status"] = "completed"
            
            logger.info("Workflow %s completed successfully", workflow_id)
            
    except Exception as e:
        logger.error("Workflow %s failed: %s", workflow_id, e)
        workflow["status"] = "errored"
        workflow["error"] = str(e)
        raise


# Placeholder for run_workflow_with_events (not used but kept for reference)
async def run_workflow_with_events(workflow_id: str, prompt: str, file_paths: list[str], event_generator):
    """Run workflow with SSE events (placeholder)."""
    pass


# =============================================================================
# Static Files (Serve Frontend)
# =============================================================================

# Mount static files last to catch API routes first
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
