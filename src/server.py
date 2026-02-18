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
import re
import shutil
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
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
from openai.types.shared.reasoning import Reasoning
from evaluation import run_openai_output_evaluation

# =============================================================================
# Configuration
# =============================================================================

THIS_DIR = Path(__file__).resolve().parent


def _detect_repo_root(start: Path) -> Path:
    """
    Detect the repo root for this project.

    When server.py is in src/, the repo root is the parent directory.
    We detect this by looking for index.html (frontend) and data/ folder.
    """
    # If we're in src/, go up one level
    if start.name == "src":
        candidate = start.parent
    else:
        candidate = start
    
    # Verify we found the repo root by checking for key files/folders
    if (candidate / "index.html").is_file() or (candidate / "data").is_dir():
        return candidate
    
    return start.parent  # Fallback: assume we're in src/


REPO_ROOT = _detect_repo_root(THIS_DIR)
PROJECT_ROOT = REPO_ROOT
STATIC_DIR = REPO_ROOT  # Frontend files are in repo root (index.html, etc.)
ENV_PATH = REPO_ROOT / ".env"
DATA_DIR = REPO_ROOT / "data"
PROMPTS_DIR = DATA_DIR / "prompts"
SKILLS_DIR = REPO_ROOT / "skills"
AGENTS_CONFIG_PATH = DATA_DIR / "agents.json"
OUTPUTS_DIR = REPO_ROOT / "outputs"
UPLOADS_DIR = REPO_ROOT / "uploads"
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt"}
MAX_UPLOAD_FILES = 5
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
WORKFLOW_TTL_SECONDS = int(os.getenv("WORKFLOW_TTL_SECONDS", str(24 * 60 * 60)))
WORKFLOW_MAX_ENTRIES = int(os.getenv("WORKFLOW_MAX_ENTRIES", "200"))

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

THEME_ALLOWED_VALUES = {
    "life_science",
    "engineering",
    "everyday_activity",
    "physics_abstract",
    "",
}

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


def load_agents_config() -> dict[str, Any]:
    """Load agent configuration from data/agents.json."""
    if not AGENTS_CONFIG_PATH.exists():
        logger.warning("Agents config not found: %s", AGENTS_CONFIG_PATH)
        return {}
    try:
        data = json.loads(AGENTS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to load agents config: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _is_under_directory(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


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
    theme: str = ""
    topic_tags: list[str]


class NewQuestionItem(BaseModel):
    id: int
    source_id: str
    stem: str
    options: list[OptionItem]
    difficulty: str
    theme: str = ""
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
    evaluation: Optional[dict[str, Any]] = None


AGENT_OUTPUT_TYPES = {
    "normalizer": NormalizerOutput,
    "extracter": ExtracterOutput,
    "verifier": VerifierOutput,
    "generator": GeneratorOutput,
    "latex": LaTeXOutput,
}


def _normalize_theme_value(theme: str) -> str:
    normalized = (theme or "").strip().lower()
    return normalized if normalized in THEME_ALLOWED_VALUES else "physics_abstract"


def _reconcile_verified_questions(
    extracted_questions: list[SourceQuestionItem],
    verified_questions: list[SourceQuestionItem],
) -> list[SourceQuestionItem]:
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
# In-Memory Storage for Workflows
# =============================================================================

workflows: dict[str, dict] = {}


def _workflow_now() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _touch_workflow(workflow: dict[str, Any]) -> None:
    workflow["updated_at"] = _workflow_now()


def _prune_workflows() -> None:
    """Bound memory by removing expired/old workflow records."""
    now = datetime.utcnow().timestamp()
    expired_ids: list[str] = []
    for workflow_id, workflow in workflows.items():
        updated_raw = workflow.get("updated_at")
        if not updated_raw:
            continue
        try:
            updated_ts = datetime.fromisoformat(updated_raw.replace("Z", "")).timestamp()
        except Exception:
            continue
        if now - updated_ts > WORKFLOW_TTL_SECONDS:
            expired_ids.append(workflow_id)

    for workflow_id in expired_ids:
        workflows.pop(workflow_id, None)

    if len(workflows) <= WORKFLOW_MAX_ENTRIES:
        return

    # Drop oldest terminal workflows first, then oldest overall.
    candidates = sorted(
        workflows.items(),
        key=lambda item: item[1].get("updated_at", ""),
    )
    for workflow_id, workflow in candidates:
        status = workflow.get("status")
        if status in {"completed", "errored"} or len(workflows) > WORKFLOW_MAX_ENTRIES:
            workflows.pop(workflow_id, None)
        if len(workflows) <= WORKFLOW_MAX_ENTRIES:
            break


# =============================================================================
# Agent Creation
# =============================================================================


def create_agents(file_ids: list[str] = None) -> dict[str, Agent]:
    """Create all agents with config from agents.json."""
    file_ids = file_ids or []
    agents_config = load_agents_config()

    code_interpreter = CodeInterpreterTool(
        tool_config={
            "type": "code_interpreter",
            "container": {
                "type": "auto",
                "file_ids": file_ids,
            },
        }
    )

    agents: dict[str, Agent] = {}
    for agent_key, config in agents_config.items():
        output_type = AGENT_OUTPUT_TYPES.get(agent_key)
        if not output_type:
            logger.warning("No output type defined for agent: %s", agent_key)
            continue

        reasoning_level = config.get("reasoning", "none")
        tools = [code_interpreter] if config.get("useCodeInterpreter", False) else []
        agents[agent_key] = Agent(
            name=config.get("name", agent_key.title()),
            instructions=load_prompt(agent_key),
            model=config.get("model", "gpt-4o"),
            tools=tools,
            output_type=output_type,
            model_settings=ModelSettings(
                store=True,
                reasoning=Reasoning(effort=reasoning_level, summary="auto"),
            ),
        )

    missing = [k for k in AGENT_OUTPUT_TYPES.keys() if k not in agents]
    if missing:
        raise ValueError(f"Missing required agent config(s): {', '.join(missing)}")
    return agents


# =============================================================================
# API Endpoints
# =============================================================================


@app.post("/api/upload")
async def upload_files(files: list[UploadFile] = File(...)):
    """Upload files for processing."""
    _prune_workflows()
    logger.info("Upload request received with %d file(s)", len(files))
    uploaded_files = []
    if not files:
        raise HTTPException(status_code=400, detail="No files uploaded")
    if len(files) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"Too many files. Maximum is {MAX_UPLOAD_FILES}.",
        )

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
                extension = Path(safe_filename).suffix.lower()
                if extension not in ALLOWED_UPLOAD_EXTENSIONS:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Unsupported file type for {safe_filename}",
                    )
                file_path = session_dir / safe_filename
                logger.info("Saving file: %s", safe_filename)
                
                try:
                    total_size = 0
                    with open(file_path, "wb") as f:
                        while True:
                            chunk = await file.read(1024 * 1024)
                            if not chunk:
                                break
                            total_size += len(chunk)
                            if total_size > MAX_UPLOAD_BYTES:
                                raise HTTPException(
                                    status_code=413,
                                    detail=f"File too large: {safe_filename}. Max size is {MAX_UPLOAD_BYTES} bytes.",
                                )
                            f.write(chunk)
                    uploaded_files.append({
                        "name": safe_filename,
                        "path": str(file_path),
                        "size": total_size,
                    })
                    logger.info("Saved file %s (%d bytes)", safe_filename, total_size)
                except Exception as file_error:
                    if file_path.exists():
                        file_path.unlink(missing_ok=True)
                    logger.error("Failed to save file %s: %s", safe_filename, file_error)
                    if isinstance(file_error, HTTPException):
                        raise
                    raise HTTPException(
                        status_code=500,
                        detail=f"Failed to save file {safe_filename}: {str(file_error)}"
                    )
                finally:
                    await file.close()
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
    sliders_path = DATA_DIR / "sliders.json"
    if not sliders_path.exists():
        raise HTTPException(status_code=404, detail="Sliders configuration not found")
    
    try:
        return json.loads(sliders_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load sliders: {str(e)}")


@app.get("/api/agents")
async def get_agents():
    """Get the agents configuration for the frontend."""
    agents_path = DATA_DIR / "agents.json"
    if not agents_path.exists():
        raise HTTPException(status_code=404, detail="Agents configuration not found")
    
    try:
        return json.loads(agents_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load agents: {str(e)}")


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
        return PlainTextResponse(prompt_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load prompt: {str(e)}")


@app.get("/api/skills")
async def get_skills():
    """Get available skill folders that contain SKILL.md."""
    if not SKILLS_DIR.exists():
        raise HTTPException(status_code=404, detail="Skills directory not found")

    try:
        skills = []
        for entry in sorted(SKILLS_DIR.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("_"):
                continue
            if (entry / "SKILL.md").exists():
                skills.append(entry.name)
        return {"skills": skills}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load skills: {str(e)}")


@app.get("/api/skills/{skill_name}")
async def get_skill(skill_name: str):
    """Get SKILL.md content for a specific skill."""
    if not re.fullmatch(r"[a-z0-9-]+", skill_name):
        raise HTTPException(status_code=404, detail="Skill not found")

    skill_path = (SKILLS_DIR / skill_name / "SKILL.md").resolve()
    if not _is_under_directory(skill_path, SKILLS_DIR):
        raise HTTPException(status_code=400, detail="Invalid skill path")
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail="Skill file not found")

    try:
        return PlainTextResponse(skill_path.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load skill: {str(e)}")


@app.get("/api/workflow/{workflow_id}/status")
async def get_workflow_status(workflow_id: str):
    """Get the current status of a workflow."""
    _prune_workflows()
    if workflow_id not in workflows:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    workflow = workflows[workflow_id]
    _touch_workflow(workflow)
    return {
        "workflowId": workflow_id,
        "status": workflow.get("status", "unknown"),
        "agents": workflow.get("agents", {}),
        "outputFolder": workflow.get("outputFolder", ""),
    }


@app.get("/api/workflow/{workflow_id}/result")
async def get_workflow_result(workflow_id: str):
    """Get the final result of a workflow."""
    _prune_workflows()
    if workflow_id not in workflows:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    workflow = workflows[workflow_id]
    _touch_workflow(workflow)
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
    _prune_workflows()
    if workflow_id not in workflows:
        raise HTTPException(status_code=404, detail="Workflow not found")
    
    workflow = workflows[workflow_id]
    _touch_workflow(workflow)
    output_folder = workflow.get("outputFolder")
    if not output_folder:
        raise HTTPException(status_code=404, detail="Output folder not found")

    safe_filename = Path(filename).name
    if safe_filename != filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    output_dir = (OUTPUTS_DIR / output_folder).resolve()
    file_path = (output_dir / safe_filename).resolve()
    if not _is_under_directory(file_path, output_dir):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    
    return FileResponse(
        path=file_path,
        filename=safe_filename,
        media_type="application/octet-stream",
    )


@app.post("/api/workflow/start")
async def start_workflow(
    prompt: str = Form(""),
    sessionId: str = Form(""),
    filePaths: str = Form("[]"),
):
    """Start a new workflow and return a stream of status updates."""
    _prune_workflows()
    workflow_id = str(uuid.uuid4())[:8]
    
    # Parse legacy file paths input, but only trust paths under uploads/.
    try:
        raw_file_paths = json.loads(filePaths) if filePaths else []
    except json.JSONDecodeError:
        raw_file_paths = []
    if not isinstance(raw_file_paths, list):
        raw_file_paths = []
    file_paths: list[str] = []
    for file_path in raw_file_paths:
        try:
            path = Path(str(file_path)).resolve()
        except Exception:
            continue
        if _is_under_directory(path, UPLOADS_DIR) and path.is_file():
            file_paths.append(str(path))
    
    # If sessionId provided, get files from upload directory
    if sessionId:
        safe_session_id = Path(sessionId).name
        if safe_session_id != sessionId:
            raise HTTPException(status_code=400, detail="Invalid sessionId")
        session_dir = (UPLOADS_DIR / safe_session_id).resolve()
        if not _is_under_directory(session_dir, UPLOADS_DIR):
            raise HTTPException(status_code=400, detail="Invalid session path")
        if session_dir.exists():
            file_paths = [str(f.resolve()) for f in session_dir.iterdir() if f.is_file()]
    
    # Initialize workflow state
    now = _workflow_now()
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
        "created_at": now,
        "updated_at": now,
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
            _touch_workflow(workflow)
            
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
            _touch_workflow(workflow)
            
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
            workflow["agents"]["normalizer"]["data"]["receivedFrom"] = "user"
            workflow["agents"]["normalizer"]["data"]["sentTo"] = "extracter"
            workflow["agents"]["normalizer"]["data"]["input"] = normalizer_input
            workflow["agents"]["normalizer"]["data"]["output"] = normalizer_output.model_dump_json()
            _touch_workflow(workflow)
            
            # Save normalizer output
            (outputs_dir / "1_normalizer.json").write_text(
                json.dumps({
                    "agent_name": "Normalizer",
                    "execution_time_seconds": elapsed,
                    "received_from_node": "user",
                    "sent_to_node": "extracter",
                    "input_messages": normalizer_input,
                    "output": normalizer_output.model_dump(),
                }, indent=2),
                encoding="utf-8"
            )
            
            # =================================================================
            # Step 2: Extracter
            # =================================================================
            workflow["agents"]["extracter"]["status"] = "thinking"
            workflow["agents"]["extracter"]["data"]["startTime"] = datetime.now().isoformat()
            _touch_workflow(workflow)
            
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
- Multiple-choice questions must not be skipped
- For multiple choice, extract all options with their keys (A, B, C, D)
- Assign a theme to each question: life_science | engineering | everyday_activity | physics_abstract
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
                retry_extract_result = await Runner.run(
                    agents["extracter"], input=retry_extract_input, run_config=run_config
                )
                extracter_output = retry_extract_result.final_output
            for q in extracter_output.questions:
                if q.options:
                    q.question_type = "multiple_choice"
                q.theme = _normalize_theme_value(q.theme)
            if file_ids and not extracter_output.questions:
                raise ValueError("Extractor returned zero questions after retry.")
            
            workflow["agents"]["extracter"]["status"] = "completed"
            workflow["agents"]["extracter"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["extracter"]["data"]["executionTime"] = elapsed
            workflow["agents"]["extracter"]["data"]["receivedFrom"] = "normalizer"
            workflow["agents"]["extracter"]["data"]["sentTo"] = "verifier"
            workflow["agents"]["extracter"]["data"]["input"] = extracter_input
            workflow["agents"]["extracter"]["data"]["output"] = f"Extracted {len(extracter_output.questions)} questions"
            _touch_workflow(workflow)
            
            # Save extracter output
            (outputs_dir / "2_extracter.json").write_text(
                json.dumps({
                    "agent_name": "Extracter",
                    "execution_time_seconds": elapsed,
                    "received_from_node": "normalizer",
                    "sent_to_node": "verifier",
                    "input_messages": extracter_input,
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
            _touch_workflow(workflow)
            
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
            
            start_time = time.time()
            verifier_result = await Runner.run(agents["verifier"], input=verifier_input, run_config=run_config)
            elapsed = time.time() - start_time
            verifier_output: VerifierOutput = verifier_result.final_output
            verifier_output.questions = _reconcile_verified_questions(
                extracter_output.questions,
                verifier_output.questions,
            )
            
            workflow["agents"]["verifier"]["status"] = "completed"
            workflow["agents"]["verifier"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["verifier"]["data"]["executionTime"] = elapsed
            workflow["agents"]["verifier"]["data"]["receivedFrom"] = "extracter"
            workflow["agents"]["verifier"]["data"]["sentTo"] = "generator"
            workflow["agents"]["verifier"]["data"]["input"] = verifier_input
            workflow["agents"]["verifier"]["data"]["output"] = f"Verified {len(verifier_output.questions)} questions"
            _touch_workflow(workflow)
            
            # Save verifier output
            (outputs_dir / "3_verifier.json").write_text(
                json.dumps({
                    "agent_name": "Verifier",
                    "execution_time_seconds": elapsed,
                    "received_from_node": "extracter",
                    "sent_to_node": "generator",
                    "input_messages": verifier_input,
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
            _touch_workflow(workflow)
            
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
            
            start_time = time.time()
            generator_result = await Runner.run(agents["generator"], input=generator_input, run_config=run_config)
            elapsed = time.time() - start_time
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
                retry_result = await Runner.run(agents["generator"], input=retry_input, run_config=run_config)
                retry_output: GeneratorOutput = retry_result.final_output
                for q in retry_output.isomorphic_questions:
                    q.theme = _normalize_theme_value(q.theme)
                generator_output.isomorphic_questions.extend(retry_output.isomorphic_questions)
                missing_source_ids = _missing_generation_source_ids(
                    verifier_output.questions,
                    generator_output.isomorphic_questions,
                    normalizer_output.new_questions_per_question,
                )
                if missing_source_ids:
                    raise ValueError(
                        f"Generator failed to produce required outputs for source_id(s): {', '.join(missing_source_ids)}"
                    )
            
            workflow["agents"]["generator"]["status"] = "completed"
            workflow["agents"]["generator"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["generator"]["data"]["executionTime"] = elapsed
            workflow["agents"]["generator"]["data"]["receivedFrom"] = "verifier"
            workflow["agents"]["generator"]["data"]["sentTo"] = "latex"
            workflow["agents"]["generator"]["data"]["input"] = generator_input
            workflow["agents"]["generator"]["data"]["output"] = f"Generated {len(generator_output.isomorphic_questions)} questions"
            _touch_workflow(workflow)
            
            # Save generator output
            (outputs_dir / "4_generator.json").write_text(
                json.dumps({
                    "agent_name": "Generator",
                    "execution_time_seconds": elapsed,
                    "received_from_node": "verifier",
                    "sent_to_node": "latex",
                    "input_messages": generator_input,
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
            _touch_workflow(workflow)
            
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
            workflow["agents"]["latex"]["data"]["receivedFrom"] = "generator"
            workflow["agents"]["latex"]["data"]["sentTo"] = "user"
            workflow["agents"]["latex"]["data"]["input"] = latex_input
            workflow["agents"]["latex"]["data"]["output"] = f"Created {latex_output.filename}"
            _touch_workflow(workflow)
            
            # Save LaTeX file
            latex_filename = latex_output.filename or "isomorphic_questions.tex"
            latex_path = outputs_dir / latex_filename
            latex_path.write_text(latex_output.latex_content, encoding="utf-8")
            
            # Save LaTeX agent output
            (outputs_dir / "5_latex.json").write_text(
                json.dumps({
                    "agent_name": "LaTeX Formatter",
                    "execution_time_seconds": elapsed,
                    "received_from_node": "generator",
                    "sent_to_node": "user",
                    "input_messages": latex_input,
                    "filename": latex_filename,
                    "output": latex_output.model_dump(),
                }, indent=2),
                encoding="utf-8"
            )
            
            # =================================================================
            # Save final results
            # =================================================================
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
            eval_report_path = outputs_dir / "evaluation_report.json"
            eval_dataset_path = outputs_dir / "evaluation_dataset.jsonl"
            if eval_report_path.exists():
                workflow["files"].append({"name": "evaluation_report.json", "size": eval_report_path.stat().st_size})
            if eval_dataset_path.exists():
                workflow["files"].append({"name": "evaluation_dataset.jsonl", "size": eval_dataset_path.stat().st_size})
            workflow["result"] = final_result.model_dump()
            workflow["status"] = "completed"
            _touch_workflow(workflow)
            
            logger.info("Workflow %s completed successfully", workflow_id)
            
    except Exception as e:
        logger.error("Workflow %s failed: %s", workflow_id, e)
        workflow["status"] = "errored"
        workflow["error"] = str(e)
        _touch_workflow(workflow)
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
