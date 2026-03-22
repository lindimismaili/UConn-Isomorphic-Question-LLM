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
from urllib.error import URLError
from urllib.request import Request, urlopen

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

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
    from .evaluation import run_openai_output_evaluation
except ImportError:
    from evaluation import run_openai_output_evaluation
try:
    from .container import SharedWorkflowRuntime, allowed_domains_from_env
    from .schemas import (
        AGENT_OUTPUT_TYPES,
        ExtracterOutput,
        GeneratorOutput,
        LaTeXOutput,
        NormalizerOutput,
        WorkflowResult,
    )
    from .helpers import (
        build_json_message,
        load_agents_config,
        load_prompt,
        missing_generation_source_ids,
        reconcile_verified_questions,
        upload_file_to_openai,
    )
except ImportError:
    from container import SharedWorkflowRuntime, allowed_domains_from_env
    from schemas import (
        AGENT_OUTPUT_TYPES,
        ExtracterOutput,
        GeneratorOutput,
        LaTeXOutput,
        NormalizerOutput,
        WorkflowResult,
    )
    from helpers import (
        build_json_message,
        load_agents_config,
        load_prompt,
        missing_generation_source_ids,
        reconcile_verified_questions,
        upload_file_to_openai,
    )

# =============================================================================
# Configuration
# =============================================================================

THIS_DIR = Path(__file__).resolve().parent


BACKEND_ROOT = THIS_DIR.parent if THIS_DIR.name == "src" else THIS_DIR
REPO_ROOT = BACKEND_ROOT.parent
PROJECT_ROOT = BACKEND_ROOT
DIST_DIR = REPO_ROOT / "dist"
ENV_PATH = REPO_ROOT / ".env"
DATA_DIR = BACKEND_ROOT / "data"
PROMPTS_DIR = DATA_DIR / "prompts"
SKILLS_DIR = DATA_DIR / "skills"
AGENTS_CONFIG_PATH = DATA_DIR / "agents.json"
OUTPUTS_DIR = BACKEND_ROOT / "outputs"
UPLOADS_DIR = BACKEND_ROOT / "uploads"
EVALUATION_DIR = DATA_DIR / "evaluation"
VERSION_PATHS = [REPO_ROOT / "version.json", DIST_DIR / "version.json"]
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".docx", ".doc", ".txt"}
MAX_UPLOAD_FILES = 5
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
WORKFLOW_TTL_SECONDS = int(os.getenv("WORKFLOW_TTL_SECONDS", str(24 * 60 * 60)))
WORKFLOW_MAX_ENTRIES = int(os.getenv("WORKFLOW_MAX_ENTRIES", "200"))
OPENAI_STATUS_URL = "https://status.openai.com/api/v2/status.json"
OPENAI_STATUS_TIMEOUT_SECONDS = float(os.getenv("OPENAI_STATUS_TIMEOUT_SECONDS", "5"))
BATCH_WORKFLOW_CONCURRENCY = max(1, int(os.getenv("BATCH_WORKFLOW_CONCURRENCY", "2")))

# Create directories if they don't exist
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
EVALUATION_DIR.mkdir(parents=True, exist_ok=True)


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

_openai_client: Optional[OpenAI] = None


def _has_openai_api_key() -> bool:
    """Check whether an OpenAI API key is currently configured."""
    return bool((os.getenv("OPENAI_API_KEY") or "").strip())


def get_openai_client() -> OpenAI:
    """
    Lazily initialize the OpenAI client.

    Keeping this lazy prevents the whole API from failing to start if
    OPENAI_API_KEY is missing; non-OpenAI endpoints can still function.
    """
    global _openai_client
    if _openai_client is not None:
        return _openai_client

    api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError(
            "OPENAI_API_KEY is not configured. "
            "Set it in the environment or in a .env file at the project root."
        )

    _openai_client = OpenAI(api_key=api_key)
    return _openai_client

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
    allow_credentials=False,
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
        "openai_api_key_configured": _has_openai_api_key(),
        "uploads_dir": str(UPLOADS_DIR),
        "uploads_dir_exists": UPLOADS_DIR.exists(),
        "uploads_dir_writable": os.access(UPLOADS_DIR, os.W_OK),
        "outputs_dir": str(OUTPUTS_DIR),
        "outputs_dir_exists": OUTPUTS_DIR.exists(),
        "outputs_dir_writable": os.access(OUTPUTS_DIR, os.W_OK),
    }


@app.get("/api/openai/status")
async def openai_status():
    """
    Return OpenAI public service status.

    Uses OpenAI's public status page API and marks the site as inactive only
    when indicator is major/critical outage.
    """
    try:
        req = Request(
            OPENAI_STATUS_URL,
            headers={"User-Agent": "IsomorphicQuestionGenerator/1.0"},
        )
        with urlopen(req, timeout=OPENAI_STATUS_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))

        status_payload = payload.get("status", {}) if isinstance(payload, dict) else {}
        indicator = str(status_payload.get("indicator", "unknown")).lower()
        description = str(status_payload.get("description", "Unknown"))
        openai_down = indicator in {"major", "critical"}

        return {
            "status": "ok",
            "openai_down": openai_down,
            "indicator": indicator,
            "description": description,
            "checked_at": datetime.now().isoformat(),
        }
    except (URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("OpenAI status check failed: %s", exc)
        return {
            "status": "unknown",
            "openai_down": False,
            "indicator": "unknown",
            "description": "Unable to verify OpenAI status right now.",
            "checked_at": datetime.now().isoformat(),
        }


@app.get("/version.json")
async def get_version_json():
    """
    Return version metadata for the frontend footer.

    In production this is typically written during deploy. In local/dev, return
    a stable fallback object so the UI does not generate 404 noise.
    """
    for version_path in VERSION_PATHS:
        if not version_path.exists():
            continue
        try:
            data = json.loads(version_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {"version": "dev", "published_at": None}


def _is_under_directory(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


# =============================================================================
# In-Memory Storage for Workflows
# =============================================================================

workflows: dict[str, dict] = {}
batch_jobs: dict[str, dict[str, Any]] = {}


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


def _prune_batch_jobs() -> None:
    """Bound memory by removing expired/old batch job records."""
    now = datetime.utcnow().timestamp()
    expired_ids: list[str] = []
    for batch_id, batch in batch_jobs.items():
        updated_raw = batch.get("updated_at")
        if not updated_raw:
            continue
        try:
            updated_ts = datetime.fromisoformat(updated_raw.replace("Z", "")).timestamp()
        except Exception:
            continue
        if now - updated_ts > WORKFLOW_TTL_SECONDS:
            expired_ids.append(batch_id)

    for batch_id in expired_ids:
        batch_jobs.pop(batch_id, None)

    if len(batch_jobs) <= WORKFLOW_MAX_ENTRIES:
        return

    candidates = sorted(
        batch_jobs.items(),
        key=lambda item: item[1].get("updated_at", ""),
    )
    for batch_id, batch in candidates:
        status = batch.get("status")
        if status in {"completed", "errored"} or len(batch_jobs) > WORKFLOW_MAX_ENTRIES:
            batch_jobs.pop(batch_id, None)
        if len(batch_jobs) <= WORKFLOW_MAX_ENTRIES:
            break


def _touch_batch_job(batch: dict[str, Any]) -> None:
    batch["updated_at"] = _workflow_now()


def _new_workflow_record() -> dict[str, Any]:
    now = _workflow_now()
    return {
        "status": "starting",
        "container": {
            "status": "inactive",
            "id": "",
        },
        "agents": {
            "normalizer": {"status": "inactive", "data": {}},
            "extracter": {"status": "inactive", "data": {}},
            "verifier": {"status": "inactive", "data": {}},
            "generator": {"status": "inactive", "data": {}},
            "latex": {"status": "inactive", "data": {}},
            "output": {"status": "inactive", "data": {}},
        },
        "files": [],
        "outputFolder": "",
        "result": None,
        "created_at": now,
        "updated_at": now,
    }


def _relative_output_folder(output_dir: Path) -> str:
    try:
        return output_dir.resolve().relative_to(OUTPUTS_DIR.resolve()).as_posix()
    except Exception:
        return output_dir.name


def _sanitize_output_folder_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", (name or "").strip())
    cleaned = cleaned.rstrip(". ")
    return cleaned or "submission"


def _make_unique_output_folder_name(base_name: str, used_names: set[str]) -> str:
    candidate = base_name
    suffix = 2
    while candidate in used_names:
        candidate = f"{base_name}_{suffix}"
        suffix += 1
    used_names.add(candidate)
    return candidate


def _list_evaluation_source_files() -> list[Path]:
    if not EVALUATION_DIR.exists():
        return []

    files: list[Path] = []
    for path in sorted(EVALUATION_DIR.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in ALLOWED_UPLOAD_EXTENSIONS:
            continue
        files.append(path)
    return files


CREATE_DATA_STEP_SEQUENCE: list[tuple[str, str]] = [
    ("normalizer", "Normalizer"),
    ("extracter", "Extracter"),
    ("verifier", "Verifier"),
    ("generator", "Generator"),
    ("latex", "LaTeX"),
    ("output", "Output"),
]


def _get_batch_entry_step(entry: dict[str, Any]) -> dict[str, Any]:
    total_steps = len(CREATE_DATA_STEP_SEQUENCE)
    step_index = 1
    step_key, step_name = CREATE_DATA_STEP_SEQUENCE[0]

    workflow_id = entry.get("workflowId", "")
    workflow = workflows.get(workflow_id) if workflow_id else None
    if workflow:
        agents = workflow.get("agents", {})
        for index, (candidate_key, candidate_name) in enumerate(
            CREATE_DATA_STEP_SEQUENCE,
            start=1,
        ):
            candidate_status = agents.get(candidate_key, {}).get("status", "inactive")
            if candidate_status != "completed":
                step_index = index
                step_key = candidate_key
                step_name = candidate_name
                break
        else:
            step_index = total_steps
            step_key, step_name = CREATE_DATA_STEP_SEQUENCE[-1]

    if entry.get("status") == "completed":
        step_index = total_steps
        step_key, step_name = CREATE_DATA_STEP_SEQUENCE[-1]

    return {
        "currentStepIndex": step_index,
        "totalSteps": total_steps,
        "currentAgentKey": step_key,
        "currentAgentName": step_name,
        "stepLabel": f"{step_index}/{total_steps}",
    }


def _summarize_batch_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for entry in entries:
        step = _get_batch_entry_step(entry)
        summary.append(
            {
                "name": entry.get("name", ""),
                "relativePath": entry.get("relativePath", ""),
                "folderName": entry.get("folderName", ""),
                "status": entry.get("status", "unknown"),
                "workflowId": entry.get("workflowId", ""),
                "outputFolder": entry.get("outputFolder", ""),
                "error": entry.get("error", ""),
                "currentStepIndex": step["currentStepIndex"],
                "totalSteps": step["totalSteps"],
                "currentAgentKey": step["currentAgentKey"],
                "currentAgentName": step["currentAgentName"],
                "stepLabel": step["stepLabel"],
            }
        )
    return summary


def _refresh_batch_progress(batch: dict[str, Any]) -> None:
    entries = batch.get("entries", [])
    completed = sum(1 for entry in entries if entry.get("status") == "completed")
    failed = sum(1 for entry in entries if entry.get("status") == "errored")
    batch["completedFiles"] = completed
    batch["failedFiles"] = failed
    batch["doneFiles"] = completed + failed


# =============================================================================
# Agent Creation
# =============================================================================


def create_agents(
    *,
    file_ids: Optional[list[str]] = None,
    container_id: Optional[str] = None,
    latex_compile_tool: Any | None = None,
) -> dict[str, Agent]:
    """Create all agents with config from agents.json.

    If container_id is provided, all Code Interpreter tool invocations use the
    same explicit OpenAI container (shared across agent calls in the workflow).
    """
    file_ids = file_ids or []
    agents_config = load_agents_config(AGENTS_CONFIG_PATH, logger)

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

    agents: dict[str, Agent] = {}
    for agent_key, config in agents_config.items():
        output_type = AGENT_OUTPUT_TYPES.get(agent_key)
        if not output_type:
            logger.warning("No output type defined for agent: %s", agent_key)
            continue

        reasoning_level = config.get("reasoning", "none")
        tools = [code_interpreter] if config.get("useCodeInterpreter", False) else []
        if agent_key == "latex" and latex_compile_tool is not None:
            tools.append(latex_compile_tool)
        valid_reasoning_levels = {"low", "medium", "high", "auto"}
        reasoning = (
            Reasoning(effort=reasoning_level)
            if reasoning_level in valid_reasoning_levels
            else None
        )
        agents[agent_key] = Agent(
            name=config.get("name", agent_key.title()),
            instructions=load_prompt(PROMPTS_DIR, logger, agent_key),
            model=config.get("model", "gpt-4o"),
            tools=tools,
            output_type=output_type,
            model_settings=ModelSettings(
                reasoning=reasoning,
            ),
        )

    missing = [k for k in AGENT_OUTPUT_TYPES.keys() if k not in agents]
    if missing:
        raise ValueError(f"Missing required agent config(s): {', '.join(missing)}")
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
    if prompt_name == "latex":
        prompt_path = PROMPTS_DIR / "latex_1.txt"
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


@app.get("/api/evaluation/files")
async def get_evaluation_files():
    """List selectable evaluation files from data/evaluation."""
    files: list[dict[str, Any]] = []
    try:
        for path in _list_evaluation_source_files():
            rel = path.relative_to(EVALUATION_DIR).as_posix()
            files.append(
                {
                    "name": path.name,
                    "relativePath": rel,
                    "size": path.stat().st_size,
                }
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load evaluation files: {str(e)}")

    return {"files": files}


@app.get("/api/workflow/{workflow_id}/status")
async def get_workflow_status(workflow_id: str):
    """Get the current status of a workflow."""
    _prune_workflows()
    _prune_batch_jobs()
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
    _prune_batch_jobs()
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
    _prune_batch_jobs()
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
    testFiles: str = Form("[]"),
):
    """Start a new workflow and return a stream of status updates."""
    try:
        get_openai_client()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    _prune_workflows()
    _prune_batch_jobs()
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

    # Parse selected test files from data/evaluation.
    try:
        raw_test_files = json.loads(testFiles) if testFiles else []
    except json.JSONDecodeError:
        raw_test_files = []
    if not isinstance(raw_test_files, list):
        raw_test_files = []
    for rel_path in raw_test_files:
        try:
            rel = Path(str(rel_path))
        except Exception:
            continue
        candidate = (EVALUATION_DIR / rel).resolve()
        if (
            _is_under_directory(candidate, EVALUATION_DIR)
            and candidate.is_file()
            and candidate.suffix.lower() in ALLOWED_UPLOAD_EXTENSIONS
        ):
            file_paths.append(str(candidate))
    
    # If sessionId provided, get files from upload directory
    if sessionId:
        safe_session_id = Path(sessionId).name
        if safe_session_id != sessionId:
            raise HTTPException(status_code=400, detail="Invalid sessionId")
        session_dir = (UPLOADS_DIR / safe_session_id).resolve()
        if not _is_under_directory(session_dir, UPLOADS_DIR):
            raise HTTPException(status_code=400, detail="Invalid session path")
        if session_dir.exists():
            file_paths.extend(str(f.resolve()) for f in session_dir.iterdir() if f.is_file())

    # Deduplicate while preserving order.
    deduped_file_paths: list[str] = []
    seen_paths: set[str] = set()
    for path in file_paths:
        if path in seen_paths:
            continue
        seen_paths.add(path)
        deduped_file_paths.append(path)
    file_paths = deduped_file_paths
    
    # Initialize workflow state
    workflows[workflow_id] = _new_workflow_record()
    
    # Start workflow in background
    asyncio.create_task(run_workflow_async(workflow_id, prompt, file_paths))
    
    return {"success": True, "workflowId": workflow_id}


@app.post("/api/create-data/start")
async def start_create_data(prompt: str = Form("")):
    """Start a batch workflow run for every file in data/evaluation."""
    try:
        get_openai_client()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    _prune_workflows()
    _prune_batch_jobs()

    evaluation_files = _list_evaluation_source_files()
    if not evaluation_files:
        raise HTTPException(status_code=404, detail="No evaluation files found")

    used_folder_names: set[str] = set()
    entries: list[dict[str, Any]] = []
    for path in evaluation_files:
        folder_name = _make_unique_output_folder_name(
            _sanitize_output_folder_name(path.stem),
            used_folder_names,
        )
        entries.append(
            {
                "name": path.name,
                "relativePath": path.relative_to(EVALUATION_DIR).as_posix(),
                "folderName": folder_name,
                "status": "queued",
                "workflowId": "",
                "outputFolder": "",
                "error": "",
            }
        )

    batch_id = str(uuid.uuid4())[:8]
    now = _workflow_now()
    batch_jobs[batch_id] = {
        "status": "starting",
        "prompt": (prompt or "").strip(),
        "totalFiles": len(entries),
        "completedFiles": 0,
        "failedFiles": 0,
        "doneFiles": 0,
        "outputFolder": "",
        "entries": entries,
        "summaryFile": "",
        "error": "",
        "created_at": now,
        "updated_at": now,
    }

    asyncio.create_task(run_create_data_batch_async(batch_id))

    return {
        "success": True,
        "batchId": batch_id,
        "totalFiles": len(entries),
    }


@app.get("/api/create-data/{batch_id}/status")
async def get_create_data_status(batch_id: str):
    """Get batch create-data progress."""
    _prune_workflows()
    _prune_batch_jobs()
    if batch_id not in batch_jobs:
        raise HTTPException(status_code=404, detail="Batch job not found")

    batch = batch_jobs[batch_id]
    _refresh_batch_progress(batch)
    _touch_batch_job(batch)
    return {
        "batchId": batch_id,
        "status": batch.get("status", "unknown"),
        "totalFiles": batch.get("totalFiles", 0),
        "completedFiles": batch.get("completedFiles", 0),
        "failedFiles": batch.get("failedFiles", 0),
        "doneFiles": batch.get("doneFiles", 0),
        "outputFolder": batch.get("outputFolder", ""),
        "entries": _summarize_batch_entries(batch.get("entries", [])),
        "error": batch.get("error", ""),
    }


@app.get("/api/create-data/{batch_id}/result")
async def get_create_data_result(batch_id: str):
    """Get the final result of a create-data batch run."""
    _prune_workflows()
    _prune_batch_jobs()
    if batch_id not in batch_jobs:
        raise HTTPException(status_code=404, detail="Batch job not found")

    batch = batch_jobs[batch_id]
    _refresh_batch_progress(batch)
    _touch_batch_job(batch)
    if batch.get("status") not in {"completed", "errored"}:
        raise HTTPException(status_code=400, detail="Batch job not yet completed")

    return {
        "batchId": batch_id,
        "status": batch.get("status", "unknown"),
        "totalFiles": batch.get("totalFiles", 0),
        "completedFiles": batch.get("completedFiles", 0),
        "failedFiles": batch.get("failedFiles", 0),
        "doneFiles": batch.get("doneFiles", 0),
        "outputFolder": batch.get("outputFolder", ""),
        "summaryFile": batch.get("summaryFile", ""),
        "entries": _summarize_batch_entries(batch.get("entries", [])),
        "error": batch.get("error", ""),
    }


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
    timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
    outputs_dir = OUTPUTS_DIR / timestamp
    await _execute_workflow(
        workflow_id,
        workflow,
        prompt,
        file_paths,
        outputs_dir=outputs_dir,
        output_folder_label=timestamp,
    )


# =============================================================================
# Batch Workflow Helpers
# =============================================================================


async def _execute_workflow(
    workflow_id: str,
    workflow: dict[str, Any],
    prompt: str,
    file_paths: list[str],
    *,
    outputs_dir: Path,
    output_folder_label: str,
) -> None:
    """Run the workflow and write artifacts into a specific output directory."""
    client = get_openai_client()

    try:
        with trace("Isomorphic Question Generation Workflow"):
            outputs_dir.mkdir(parents=True, exist_ok=True)
            workflow["outputFolder"] = output_folder_label
            workflow["status"] = "running"
            _touch_workflow(workflow)

            run_config = RunConfig(
                trace_metadata={
                    "__trace_source__": "isomorphic-question-generator",
                    "timestamp": output_folder_label.replace("/", "_"),
                }
            )
            agent_outputs: list[dict[str, Any]] = []

            file_ids = []
            file_names = []
            for file_path in file_paths:
                path = Path(file_path)
                if path.exists():
                    file_id = upload_file_to_openai(client, path, logger)
                    file_ids.append(file_id)
                    file_names.append(path.name)

            shared_container_id: Optional[str] = None
            if not hasattr(client, "containers"):
                raise RuntimeError(
                    "OpenAI SDK does not support containers; upgrade openai to >=2.0.0"
                )
            workflow["container"]["status"] = "creating"
            _touch_workflow(workflow)
            container = client.containers.create(
                name=f"isomorphic-workflow-{workflow_id}-{output_folder_label.replace('/', '-')}",
                expires_after={"anchor": "last_active_at", "minutes": _container_expires_minutes()},
                file_ids=file_ids,
            )
            shared_container_id = container.id
            workflow["container"]["status"] = "bootstrapping"
            workflow["container"]["id"] = shared_container_id
            workflow["container"]["allowedDomains"] = allowed_domains_from_env()
            _touch_workflow(workflow)
            shared_runtime = SharedWorkflowRuntime(
                client=client,
                container_id=shared_container_id,
                output_dir=outputs_dir,
                allowed_domains=allowed_domains_from_env(),
            )
            shared_runtime.bootstrap()
            workflow["container"]["status"] = "active"
            _touch_workflow(workflow)

            agents = create_agents(
                file_ids=file_ids,
                container_id=shared_container_id,
                latex_compile_tool=shared_runtime.build_compile_latex_tool(),
            )

            workflow["agents"]["normalizer"]["status"] = "thinking"
            workflow["agents"]["normalizer"]["data"]["startTime"] = datetime.now().isoformat()
            _touch_workflow(workflow)

            prompt_text = (prompt or "").strip()
            normalizer_input_payload = {
                "task": "normalize_user_request",
                "user_input": {
                    "prompt": prompt_text,
                    "files": file_names,
                },
                "defaults": {
                    "new_questions_per_question": 1,
                    "difficulty": "",
                    "title": "",
                },
            }
            normalizer_input = build_json_message(normalizer_input_payload)
            workflow["agents"]["normalizer"]["data"]["receivedFrom"] = "user"
            workflow["agents"]["normalizer"]["data"]["sentTo"] = "extracter"
            workflow["agents"]["normalizer"]["data"]["input"] = (
                {"prompt": prompt_text} if prompt_text else None
            )
            _touch_workflow(workflow)

            start_time = time.time()
            normalizer_result = await Runner.run(
                agents["normalizer"], input=normalizer_input, run_config=run_config
            )
            elapsed = time.time() - start_time
            normalizer_output: NormalizerOutput = normalizer_result.final_output

            workflow["agents"]["normalizer"]["status"] = "completed"
            workflow["agents"]["normalizer"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["normalizer"]["data"]["executionTime"] = elapsed
            workflow["agents"]["normalizer"]["data"]["output"] = normalizer_output.model_dump()
            workflow["agents"]["extracter"]["status"] = "thinking"
            workflow["agents"]["extracter"]["data"]["startTime"] = datetime.now().isoformat()
            _touch_workflow(workflow)
            agent_outputs.append(
                {
                    "agent_name": "Normalizer",
                    "execution_time_seconds": elapsed,
                    "received_from_node": "user",
                    "sent_to_node": "extracter",
                    "input_messages": normalizer_input,
                    "output": normalizer_output.model_dump(),
                }
            )

            file_refs = [{"type": "input_file", "file_id": fid} for fid in file_ids]
            extracter_input_payload = {
                "task": "extract_source_questions",
                "files": file_names,
                "normalizer_config": normalizer_output.model_dump(),
                "requirements": {
                    "preserve_math_latex": True,
                    "extract_metadata": ["title", "author", "date", "course"],
                    "must_include_multiple_choice": True,
                    "multiple_choice_option_keys": ["A", "B", "C", "D"],
                    "set_has_explicit_answer_only_if_marked": True,
                },
            }
            extracter_input = build_json_message(extracter_input_payload, file_refs=file_refs)
            workflow["agents"]["extracter"]["data"]["receivedFrom"] = "normalizer"
            workflow["agents"]["extracter"]["data"]["sentTo"] = "verifier"
            workflow["agents"]["extracter"]["data"]["input"] = extracter_input_payload
            _touch_workflow(workflow)

            start_time = time.time()
            extracter_result = await Runner.run(
                agents["extracter"], input=extracter_input, run_config=run_config
            )
            extracter_output: ExtracterOutput = extracter_result.final_output
            if file_ids and not extracter_output.questions:
                retry_extract_payload = {
                    "task": "extract_source_questions_retry",
                    "reason": "previous_result_had_zero_questions",
                    "files": file_names,
                    "requirements": {
                        "must_include_multiple_choice": True,
                    },
                }
                retry_extract_input = build_json_message(retry_extract_payload, file_refs=file_refs)
                retry_extract_result = await Runner.run(
                    agents["extracter"], input=retry_extract_input, run_config=run_config
                )
                extracter_output = retry_extract_result.final_output
            elapsed = time.time() - start_time
            for q in extracter_output.questions:
                if q.options:
                    q.question_type = "multiple_choice"
            if file_ids and not extracter_output.questions:
                raise ValueError("Extractor returned zero questions after retry.")

            workflow["agents"]["extracter"]["status"] = "completed"
            workflow["agents"]["extracter"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["extracter"]["data"]["executionTime"] = elapsed
            workflow["agents"]["extracter"]["data"]["output"] = extracter_output.model_dump()
            workflow["agents"]["verifier"]["status"] = "thinking"
            workflow["agents"]["verifier"]["data"]["startTime"] = datetime.now().isoformat()
            _touch_workflow(workflow)
            agent_outputs.append(
                {
                    "agent_name": "Extracter",
                    "execution_time_seconds": elapsed,
                    "received_from_node": "normalizer",
                    "sent_to_node": "verifier",
                    "input_messages": extracter_input,
                    "questions_count": len(extracter_output.questions),
                    "output": extracter_output.model_dump(),
                }
            )

            verifier_input_payload = {
                "task": "verify_source_questions",
                "source_questions": extracter_output.model_dump(),
                "requirements": {
                    "preserve_all_questions": True,
                    "if_answer_marked_keep_it": True,
                    "if_unmarked_solve_and_mark_correct_option": True,
                    "add_option_explanations": True,
                    "use_code_interpreter_for_calculations": True,
                    "solve_open_ended_and_provide_answer": True,
                    "solve_multi_part_questions_per_part": True,
                },
            }
            verifier_input = build_json_message(verifier_input_payload)
            workflow["agents"]["verifier"]["data"]["receivedFrom"] = "extracter"
            workflow["agents"]["verifier"]["data"]["sentTo"] = "generator"
            workflow["agents"]["verifier"]["data"]["input"] = verifier_input_payload
            _touch_workflow(workflow)

            start_time = time.time()
            verifier_result = await Runner.run(
                agents["verifier"], input=verifier_input, run_config=run_config
            )
            elapsed = time.time() - start_time
            verifier_output: VerifierOutput = verifier_result.final_output
            verifier_output.questions = reconcile_verified_questions(
                extracter_output.questions,
                verifier_output.questions,
            )

            workflow["agents"]["verifier"]["status"] = "completed"
            workflow["agents"]["verifier"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["verifier"]["data"]["executionTime"] = elapsed
            workflow["agents"]["verifier"]["data"]["output"] = verifier_output.model_dump()
            workflow["agents"]["generator"]["status"] = "thinking"
            workflow["agents"]["generator"]["data"]["startTime"] = datetime.now().isoformat()
            _touch_workflow(workflow)
            agent_outputs.append(
                {
                    "agent_name": "Verifier",
                    "execution_time_seconds": elapsed,
                    "received_from_node": "extracter",
                    "sent_to_node": "generator",
                    "input_messages": verifier_input,
                    "questions_count": len(verifier_output.questions),
                    "output": verifier_output.model_dump(),
                }
            )

            generator_input_payload = {
                "task": "generate_isomorphic_questions",
                "configuration": normalizer_output.model_dump(),
                "source_questions": verifier_output.model_dump(),
                "requirements": {
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
                },
            }
            generator_input = build_json_message(generator_input_payload)
            workflow["agents"]["generator"]["data"]["receivedFrom"] = "verifier"
            workflow["agents"]["generator"]["data"]["sentTo"] = "latex"
            workflow["agents"]["generator"]["data"]["input"] = generator_input_payload
            _touch_workflow(workflow)

            start_time = time.time()
            generator_result = await Runner.run(
                agents["generator"], input=generator_input, run_config=run_config
            )
            generator_output: GeneratorOutput = generator_result.final_output

            missing_source_ids = missing_generation_source_ids(
                verifier_output.questions,
                generator_output.isomorphic_questions,
                normalizer_output.new_questions_per_question,
            )
            if missing_source_ids:
                retry_questions = [
                    q for q in verifier_output.questions if q.source_id in missing_source_ids
                ]
                retry_payload = {
                    "task": "generate_isomorphic_questions_retry",
                    "reason": "missing_required_source_ids",
                    "missing_source_ids": missing_source_ids,
                    "new_questions_per_source": normalizer_output.new_questions_per_question,
                    "configuration": normalizer_output.model_dump(),
                    "source_questions": {"questions": [q.model_dump() for q in retry_questions]},
                }
                retry_input = build_json_message(retry_payload)
                retry_result = await Runner.run(
                    agents["generator"], input=retry_input, run_config=run_config
                )
                retry_output: GeneratorOutput = retry_result.final_output
                missing_source_ids_set = set(missing_source_ids)
                generator_output.isomorphic_questions.extend(
                    q for q in retry_output.isomorphic_questions
                    if q.source_id in missing_source_ids_set
                )
                missing_source_ids = missing_generation_source_ids(
                    verifier_output.questions,
                    generator_output.isomorphic_questions,
                    normalizer_output.new_questions_per_question,
                )
                if missing_source_ids:
                    raise ValueError(
                        "Generator failed to produce required outputs for source_id(s): "
                        + ", ".join(missing_source_ids)
                    )
            elapsed = time.time() - start_time

            workflow["agents"]["generator"]["status"] = "completed"
            workflow["agents"]["generator"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["generator"]["data"]["executionTime"] = elapsed
            workflow["agents"]["generator"]["data"]["output"] = generator_output.model_dump()
            workflow["agents"]["latex"]["status"] = "thinking"
            workflow["agents"]["latex"]["data"]["startTime"] = datetime.now().isoformat()
            _touch_workflow(workflow)
            agent_outputs.append(
                {
                    "agent_name": "Generator",
                    "execution_time_seconds": elapsed,
                    "received_from_node": "verifier",
                    "sent_to_node": "latex",
                    "input_messages": generator_input,
                    "questions_count": len(generator_output.isomorphic_questions),
                    "output": generator_output.model_dump(),
                }
            )

            questions_for_latex = []
            for q in generator_output.isomorphic_questions:
                questions_for_latex.append(
                    {
                        "source_id": q.source_id,
                        "stem": q.stem,
                        "options": [opt.model_dump() for opt in q.options],
                        "question_type": q.question_type,
                        "skipped": q.skipped,
                        "skip_reason": q.skip_reason,
                        "answer": q.answer,
                        "parts": [part.model_dump() for part in q.parts],
                    }
                )

            latex_input_payload = {
                "task": "format_questions_to_latex",
                "questions": questions_for_latex,
                "requirements": {
                    "include_document_preamble": True,
                    "include_macros": [
                        "\\QBlock",
                        "\\Correct",
                        "\\Wrong",
                        "\\OEBlock",
                        "\\MPBlock",
                        "\\MCPart",
                        "\\OEPart",
                        "\\SkipBlock",
                    ],
                    "format_all_questions_with_macros": True,
                    "proper_math_formatting": True,
                    "filename": "isomorphic_questions.tex",
                },
            }
            latex_input = build_json_message(latex_input_payload)
            workflow["agents"]["latex"]["data"]["receivedFrom"] = "generator"
            workflow["agents"]["latex"]["data"]["sentTo"] = "user"
            workflow["agents"]["latex"]["data"]["input"] = latex_input_payload
            _touch_workflow(workflow)

            start_time = time.time()
            latex_result = await Runner.run(
                agents["latex"], input=latex_input, run_config=run_config
            )
            elapsed = time.time() - start_time
            latex_output: LaTeXOutput = latex_result.final_output

            workflow["agents"]["latex"]["status"] = "completed"
            workflow["agents"]["latex"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["latex"]["data"]["executionTime"] = elapsed
            workflow["agents"]["latex"]["data"]["output"] = latex_output.model_dump()
            _touch_workflow(workflow)

            latex_filename = latex_output.filename or "isomorphic_questions.tex"
            latex_path = outputs_dir / latex_filename
            latex_path.write_text(latex_output.latex_content, encoding="utf-8")
            pdf_filename = latex_output.pdf_filename or "isomorphic_questions.pdf"
            pdf_path = outputs_dir / pdf_filename
            if not pdf_path.exists():
                raise RuntimeError(
                    "LaTeX agent did not produce the compiled PDF artifact in the shared container."
                )
            agent_outputs.append(
                {
                    "agent_name": "LaTeX Formatter",
                    "execution_time_seconds": elapsed,
                    "received_from_node": "generator",
                    "sent_to_node": "user",
                    "input_messages": latex_input,
                    "filename": latex_filename,
                    "pdf_filename": pdf_filename,
                    "output": latex_output.model_dump(),
                }
            )

            output_stage_start = time.time()
            workflow["agents"]["output"]["status"] = "thinking"
            workflow["agents"]["output"]["data"]["startTime"] = datetime.now().isoformat()
            workflow["agents"]["output"]["data"]["receivedFrom"] = "latex"
            workflow["agents"]["output"]["data"]["sentTo"] = "user"
            workflow["agents"]["output"]["data"]["input"] = (
                f"Finalize local output files in outputs/{output_folder_label}"
            )
            workflow["agents"]["output"]["data"]["output"] = "Writing local artifacts..."
            _touch_workflow(workflow)

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
            )

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

            eval_report_path = outputs_dir / "evaluation_report.json"
            eval_dataset_path = outputs_dir / "evaluation_dataset.jsonl"
            agent_outputs_path = outputs_dir / "agent_outputs.json"
            files_metadata = [
                {"name": "final_result.json", "size": result_path.stat().st_size},
                {"name": latex_filename, "size": latex_path.stat().st_size},
                {"name": pdf_filename, "size": pdf_path.stat().st_size},
            ]
            if eval_report_path.exists():
                files_metadata.append(
                    {"name": "evaluation_report.json", "size": eval_report_path.stat().st_size}
                )
            if eval_dataset_path.exists():
                files_metadata.append(
                    {"name": "evaluation_dataset.jsonl", "size": eval_dataset_path.stat().st_size}
                )

            output_payload = {
                "agent_name": "Output",
                "execution_time_seconds": None,
                "received_from_node": "latex",
                "sent_to_node": "user",
                "input_summary": f"Finalize output files for workflow {workflow_id}",
                "output": {
                    "output_folder": str(outputs_dir),
                    "files": [item["name"] for item in files_metadata] + ["agent_outputs.json"],
                },
            }
            agent_outputs.append(output_payload)

            output_elapsed = time.time() - output_stage_start
            output_payload["execution_time_seconds"] = output_elapsed
            agent_outputs_path.write_text(
                json.dumps(
                    {
                        "generated_at": datetime.now().isoformat(),
                        "workflow_id": workflow_id,
                        "agents": agent_outputs,
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            files_metadata.append(
                {"name": "agent_outputs.json", "size": agent_outputs_path.stat().st_size}
            )

            workflow["files"] = files_metadata
            workflow["agents"]["output"]["status"] = "completed"
            workflow["agents"]["output"]["data"]["endTime"] = datetime.now().isoformat()
            workflow["agents"]["output"]["data"]["executionTime"] = output_elapsed
            workflow["agents"]["output"]["data"]["output"] = (
                f"Saved {len(files_metadata)} file(s) to outputs/{output_folder_label}"
            )
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


async def run_create_data_batch_async(batch_id: str):
    """Run the workflow against every evaluation file in parallel."""
    batch = batch_jobs[batch_id]
    semaphore = asyncio.Semaphore(BATCH_WORKFLOW_CONCURRENCY)

    try:
        timestamp = datetime.now().strftime("%Y_%m_%d_%H_%M_%S")
        batch_output_dir = OUTPUTS_DIR / timestamp
        batch_output_dir.mkdir(parents=True, exist_ok=True)
        batch["status"] = "running"
        batch["outputFolder"] = timestamp
        _touch_batch_job(batch)

        async def run_entry(entry: dict[str, Any]) -> None:
            async with semaphore:
                entry["status"] = "running"
                _refresh_batch_progress(batch)
                _touch_batch_job(batch)

                workflow_id = str(uuid.uuid4())[:8]
                entry["workflowId"] = workflow_id
                workflows[workflow_id] = _new_workflow_record()

                workflow = workflows[workflow_id]
                relative_path = entry.get("relativePath", "")
                source_path = (EVALUATION_DIR / relative_path).resolve()
                output_folder_label = f"{timestamp}/{entry['folderName']}"
                child_output_dir = batch_output_dir / entry["folderName"]

                try:
                    if not _is_under_directory(source_path, EVALUATION_DIR) or not source_path.is_file():
                        raise FileNotFoundError(f"Evaluation file not found: {relative_path}")

                    await _execute_workflow(
                        workflow_id,
                        workflow,
                        batch.get("prompt", ""),
                        [str(source_path)],
                        outputs_dir=child_output_dir,
                        output_folder_label=output_folder_label,
                    )
                    entry["status"] = "completed"
                    entry["outputFolder"] = output_folder_label
                    entry["error"] = ""
                except Exception as exc:
                    workflow["status"] = "errored"
                    workflow["error"] = str(exc)
                    workflow["outputFolder"] = output_folder_label
                    _touch_workflow(workflow)
                    entry["status"] = "errored"
                    entry["outputFolder"] = output_folder_label
                    entry["error"] = str(exc)
                finally:
                    _refresh_batch_progress(batch)
                    _touch_batch_job(batch)

        await asyncio.gather(*(run_entry(entry) for entry in batch.get("entries", [])))

        _refresh_batch_progress(batch)
        summary_path = batch_output_dir / "batch_summary.json"
        summary_path.write_text(
            json.dumps(
                {
                    "generated_at": datetime.now().isoformat(),
                    "batch_id": batch_id,
                    "prompt": batch.get("prompt", ""),
                    "output_folder": batch.get("outputFolder", ""),
                    "total_files": batch.get("totalFiles", 0),
                    "completed_files": batch.get("completedFiles", 0),
                    "failed_files": batch.get("failedFiles", 0),
                    "entries": _summarize_batch_entries(batch.get("entries", [])),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        batch["summaryFile"] = summary_path.name
        batch["error"] = (
            f"{batch['failedFiles']} of {batch['totalFiles']} file workflows failed."
            if batch.get("failedFiles", 0) > 0
            else ""
        )
        batch["status"] = "completed"
        _touch_batch_job(batch)
        logger.info("Create-data batch %s completed", batch_id)
    except Exception as exc:
        logger.error("Create-data batch %s failed: %s", batch_id, exc)
        batch["status"] = "errored"
        batch["error"] = str(exc)
        _touch_batch_job(batch)

# =============================================================================
# Static Files (Serve Frontend)
# =============================================================================

# Mount static files last to catch API routes first
if DIST_DIR.is_dir() and (DIST_DIR / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="static")
else:
    @app.get("/", include_in_schema=False)
    async def root():
        return HTMLResponse(
            """
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Isomorphic Question Generator</title>
  </head>
  <body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; padding: 24px;">
    <h2>Frontend not built</h2>
    <p>This FastAPI server is running, but <code>dist/index.html</code> was not found.</p>
    <p>Dev UI: run <code>npm -C frontend run dev</code> (then open the Vite URL).</p>
    <p>Prod UI: run <code>npm -C frontend run build</code>, then refresh this page.</p>
    <p>API health: <a href="/api/health">/api/health</a></p>
  </body>
</html>
            """.strip()
        )


# =============================================================================
# Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
