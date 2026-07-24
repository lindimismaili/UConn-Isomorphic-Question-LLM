"""Cloudflare Access protected operations dashboard API."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import shutil
import uuid
import zipfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from openai import OpenAI
from pydantic import BaseModel, Field

from backend.src.api.deployment_status import deployment_status_payload
from backend.src.api.state import TERMINAL_STATUSES, replace_openai_api_key, workflow_store, workflows
from backend.src.operations import (
    CODE_MANUAL,
    RUNTIME_CONFIG_PATH,
    fetch_openai_cost_summary,
    fetch_openai_status,
    operations,
)
from backend.src.paths import (
    ARCHIVE_DIR,
    DATA_DIR,
    EVALUATION_FIXTURES_DIR,
    OUTPUTS_DIR,
    RUNTIME_DIR,
    UPLOADS_DIR,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])
_SENSITIVE_SUFFIXES = {".env", ".key", ".pem", ".pfx", ".sqlite", ".sqlite3", ".db"}
_MAX_ZIP_BYTES = max(1, int(os.getenv("ADMIN_MAX_ZIP_BYTES", str(5 * 1024 * 1024 * 1024))))
_MAX_ZIP_FILES = max(1, int(os.getenv("ADMIN_MAX_ZIP_FILES", "10000")))
_DATA_FILES = frozenset({"agents.json", "latex_template.tex", "rubric.json"})
_ROOT_DISPLAY_PATHS = {
    "archive": "/archive",
    "uploads": "/site/backend/uploads",
    "outputs": "/site/backend/outputs",
    "evaluations": "/site/backend/data/evaluation",
    "data": "/site/backend/data",
}


def _configured_admins() -> set[str]:
    return {value.strip().lower() for value in os.getenv("ADMIN_EMAILS", "").split(",") if value.strip()}


def _local_development_actor(request: Request) -> str | None:
    """Return an explicitly configured local test identity, never a remote one.

    This exists solely for running the complete dashboard against local files
    without a Cloudflare tunnel. It requires both an opt-in flag and a direct
    loopback connection; reverse-proxy headers are deliberately not trusted.
    """
    enabled = os.getenv("ADMIN_LOCAL_DEV_MODE", "").strip().lower() in {"1", "true", "yes", "on"}
    email = os.getenv("ADMIN_LOCAL_DEV_EMAIL", "").strip().lower()
    client_host = request.client.host if request.client else ""
    try:
        is_loopback = ipaddress.ip_address(client_host).is_loopback
    except ValueError:
        is_loopback = False
    if enabled and is_loopback and "@" in email and len(email) <= 320:
        return email
    return None


@lru_cache(maxsize=4)
def _access_jwk_client(team_domain: str):
    try:
        import jwt
    except ImportError as exc:  # pragma: no cover - dependency absence is deployment-specific
        raise RuntimeError("PyJWT is required for Cloudflare Access validation") from exc
    return jwt.PyJWKClient(f"https://{team_domain}/cdn-cgi/access/certs")


def _verify_access_token(token: str) -> str:
    """Return the Cloudflare Access email after full signature/claim validation."""
    team_domain = os.getenv("CF_ACCESS_TEAM_DOMAIN", "").strip()
    audience = os.getenv("CF_ACCESS_AUD", "").strip()
    allowed = _configured_admins()
    if not team_domain or not audience or not allowed:
        raise HTTPException(status_code=503, detail="Admin authentication is not configured")
    if not token:
        raise HTTPException(status_code=401, detail="Cloudflare Access authentication is required")
    try:
        import jwt

        client = _access_jwk_client(team_domain)
        signing_key = client.get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=f"https://{team_domain}",
            options={"require": ["exp", "aud", "iss", "email"]},
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Invalid Cloudflare Access token") from exc
    email = str(claims.get("email", "")).strip().lower()
    if not email or email not in allowed:
        raise HTTPException(status_code=403, detail="This account is not an administrator")
    return email


def require_admin(request: Request) -> str:
    local_actor = _local_development_actor(request)
    if local_actor:
        return local_actor
    return _verify_access_token(request.headers.get("Cf-Access-Jwt-Assertion", ""))


def require_same_origin(request: Request) -> None:
    """Reject browser-originated cross-site admin writes even with a valid cookie."""
    origin = request.headers.get("origin")
    if not origin:
        return
    parsed = urlparse(origin)
    if parsed.netloc != request.headers.get("host", ""):
        raise HTTPException(status_code=403, detail="Cross-origin admin requests are not allowed")


def require_service_available() -> None:
    status = operations.public_status()
    if status["maintenance"]:
        raise HTTPException(status_code=503, detail={"code": status["code"], "message": status["message"], "detectedAt": status["detectedAt"]})


def _roots() -> dict[str, Path]:
    return {
        "archive": ARCHIVE_DIR,
        "uploads": UPLOADS_DIR,
        "outputs": OUTPUTS_DIR,
        "evaluations": EVALUATION_FIXTURES_DIR,
        "data": DATA_DIR,
    }


def _allowed_data_path(relative_path: Path) -> bool:
    """Expose only the requested hand-curated agent configuration files."""
    parts = relative_path.parts
    if not parts:
        return True
    if len(parts) == 1:
        return parts[0] in _DATA_FILES or parts[0] == "prompts"
    return len(parts) == 2 and parts[0] == "prompts" and relative_path.suffix.lower() == ".txt"


def _is_real_workflow_id(value: str) -> bool:
    try:
        uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def _safe_path(root: str, relative: str = "") -> Path:
    base = _roots().get(root)
    if base is None:
        raise HTTPException(status_code=404, detail="Unknown file root")
    relative_path = Path(relative)
    if relative_path.is_absolute() or any(part in {"", ".", ".."} or part.startswith(".") for part in relative_path.parts):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if root == "data" and not _allowed_data_path(relative_path):
        raise HTTPException(status_code=404, detail="File not available")
    candidate = base
    for part in relative_path.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise HTTPException(status_code=404, detail="Symlinked files are not available")
    try:
        path = candidate.resolve()
        path.relative_to(base.resolve())
    except (OSError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid file path") from None
    if path.suffix.lower() in _SENSITIVE_SUFFIXES:
        raise HTTPException(status_code=404, detail="File not available")
    return path


def _entry(path: Path, root: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"name": path.name, "path": path.relative_to(root).as_posix(), "isDirectory": path.is_dir(), "size": None if path.is_dir() else stat.st_size, "modifiedAt": datetime.fromtimestamp(stat.st_mtime).isoformat()}


def _directory_size(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file() and not item.is_symlink() and item.suffix.lower() not in _SENSITIVE_SUFFIXES:
                total += item.stat().st_size
    except OSError:
        return total
    return total


def _safe_zip(directory: Path, root: Path, root_name: str) -> Path:
    total = 0
    files: list[Path] = []
    for item in directory.rglob("*"):
        if not item.is_file() or item.is_symlink() or item.suffix.lower() in _SENSITIVE_SUFFIXES:
            continue
        try:
            relative_item = item.resolve().relative_to(root.resolve())
            size = item.stat().st_size
        except (OSError, ValueError):
            continue
        if root_name == "data" and not _allowed_data_path(relative_item):
            continue
        total += size
        if total > _MAX_ZIP_BYTES or len(files) >= _MAX_ZIP_FILES:
            raise HTTPException(status_code=413, detail="Requested ZIP exceeds the operations download limit")
        files.append(item)
    download_dir = RUNTIME_DIR / "admin-downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
    output = download_dir / f"{directory.name or 'folder'}-{uuid.uuid4().hex[:12]}.zip"
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in files:
            archive.write(item, item.relative_to(directory).as_posix())
    return output


class SettingsUpdate(BaseModel):
    maintenance: bool | None = None
    maintenance_message: str = Field(default="", max_length=500)
    stage_cache_enabled: bool | None = None
    cost_warning_dollars: float | None = Field(default=None, ge=0)


class KeyUpdate(BaseModel):
    api_key: str = Field(min_length=8, max_length=512)


@router.get("/session")
async def session(actor: str = Depends(require_admin)):
    return {"email": actor}


@router.get("/dashboard")
async def dashboard(request: Request, actor: str = Depends(require_admin)):
    del actor
    durable = dict(workflow_store.list_records("workflow"))
    all_records = {
        workflow_id: value
        for workflow_id, value in {**durable, **workflows}.items()
        if _is_real_workflow_id(workflow_id)
    }
    active = [(workflow_id, value) for workflow_id, value in all_records.items() if value.get("status") not in TERMINAL_STATUSES]
    recent = [
        {"id": workflow_id, "status": value.get("status", "unknown"), "createdAt": value.get("created_at", ""), "updatedAt": value.get("updated_at", ""), "outputFolder": value.get("outputFolder", ""), "error": value.get("error", "")[:240]}
        for workflow_id, value in sorted(all_records.items(), key=lambda item: item[1].get("updated_at", ""), reverse=True)[:25]
    ]
    usage = shutil.disk_usage(OUTPUTS_DIR)
    services = operations.load_probe()
    openai_status = await asyncio.to_thread(fetch_openai_status)
    requested_host = (request.url.hostname or "").lower()
    public_health = str(services.get("public_health", "not-monitored"))
    if requested_host in {"localhost", "127.0.0.1", "::1"}:
        site = {"state": "localhost", "label": "Localhost", "detail": "Running only on this computer.", "checkedAt": ""}
    elif public_health == "ok":
        site = {"state": "online", "label": "Online", "detail": "Public health probe passed.", "checkedAt": str(services.get("checked_at", ""))}
    elif public_health == "not-configured":
        site = {"state": "offline", "label": "Not online", "detail": "No public hostname is configured.", "checkedAt": str(services.get("checked_at", ""))}
    else:
        site = {"state": "offline", "label": "Public check failed", "detail": f"Public health probe: {public_health}.", "checkedAt": str(services.get("checked_at", ""))}
    tunnel = str(services.get("cloudflared", "not-monitored"))
    cloudflare_public = str(services.get("cloudflare_status", "not-monitored"))
    if tunnel == "active" and cloudflare_public == "ok":
        cloudflare = {"state": "online", "label": "Connected", "detail": "Tunnel active; Cloudflare status operational.", "checkedAt": str(services.get("checked_at", ""))}
    elif tunnel == "not-monitored":
        cloudflare = {"state": "unknown", "label": "Not monitored", "detail": "Pi operations probe has not reported yet.", "checkedAt": ""}
    else:
        cloudflare = {"state": "offline", "label": "Needs attention", "detail": f"Tunnel: {tunnel}; Cloudflare: {cloudflare_public}.", "checkedAt": str(services.get("checked_at", ""))}
    return {
        "maintenance": operations.public_status(),
        "activeWorkflows": len(active),
        "activeVisitors": operations.active_visitors(),
        "storage": {key: _directory_size(path) if path.exists() else 0 for key, path in _roots().items()},
        "disk": {"total": usage.total, "used": usage.used, "free": usage.free},
        "workflows": recent,
        "incidents": operations.incidents(),
        "audit": operations.audit_events(),
        "settings": {"stageCacheEnabled": operations.get_setting("stage_cache_enabled", None), "costWarningDollars": operations.get_setting("cost_warning_dollars", None), "openaiAdminKeyConfigured": bool(os.getenv("OPENAI_ADMIN_KEY", "").strip())},
        "cost": await asyncio.to_thread(fetch_openai_cost_summary),
        "site": site,
        "cloudflare": cloudflare,
        "openai": openai_status,
        "operations": services,
        "deployment": deployment_status_payload(active_workflows=len(active)),
    }


@router.get("/files/{root}/entries")
async def list_entries(root: str, path: str = "", actor: str = Depends(require_admin)):
    del actor
    directory = _safe_path(root, path)
    if not directory.exists() or not directory.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")
    base = _roots()[root]
    entries = []
    for item in directory.iterdir():
        if item.name.startswith(".") or item.is_symlink() or item.suffix.lower() in _SENSITIVE_SUFFIXES:
            continue
        if root == "data" and not _allowed_data_path(item.relative_to(base)):
            continue
        try:
            entries.append(_entry(item, base))
        except OSError:
            continue
    entries.sort(key=lambda item: (not item["isDirectory"], item["name"].lower()))
    return {"root": root, "path": path, "displayPath": _ROOT_DISPLAY_PATHS[root], "entries": entries}


@router.get("/files/{root}/download")
async def download_file(root: str, path: str, actor: str = Depends(require_admin)):
    operations.audit(actor, "file.download", f"{root}/{path}", "success")
    item = _safe_path(root, path)
    if not item.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(item, filename=item.name)


@router.get("/files/{root}/preview")
async def preview_file(root: str, path: str, actor: str = Depends(require_admin)):
    operations.audit(actor, "file.preview", f"{root}/{path}", "success")
    item = _safe_path(root, path)
    if not item.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(item, filename=item.name, content_disposition_type="inline")


@router.post("/files/{root}/zip")
async def download_folder_zip(root: str, request: Request, path: str = "", actor: str = Depends(require_admin)):
    require_same_origin(request)
    directory = _safe_path(root, path)
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail="Only folders can be downloaded as a ZIP")
    archive = await asyncio.to_thread(_safe_zip, directory, _roots()[root], root)
    operations.audit(actor, "folder.zip", f"{root}/{path}", "success")
    return FileResponse(archive, filename=f"{directory.name or root}.zip")


@router.post("/settings")
async def update_settings(payload: SettingsUpdate, request: Request, actor: str = Depends(require_admin)):
    require_same_origin(request)
    if payload.maintenance is not None:
        operations.set_maintenance(active=payload.maintenance, code=CODE_MANUAL, message=payload.maintenance_message, actor=actor)
    if payload.stage_cache_enabled is not None:
        operations.set_setting("stage_cache_enabled", payload.stage_cache_enabled)
        operations.audit(actor, "stage-cache.update", "enabled", "success")
    if payload.cost_warning_dollars is not None:
        operations.set_setting("cost_warning_dollars", payload.cost_warning_dollars)
        operations.audit(actor, "cost-alert.update", "warning-threshold", "success")
    return {"maintenance": operations.public_status(), "stageCacheEnabled": operations.get_setting("stage_cache_enabled", None), "costWarningDollars": operations.get_setting("cost_warning_dollars", None)}


@router.post("/openai-key")
async def update_openai_key(payload: KeyUpdate, request: Request, actor: str = Depends(require_admin)):
    require_same_origin(request)
    candidate = payload.api_key.strip()
    try:
        await asyncio.to_thread(lambda: OpenAI(api_key=candidate).models.list())
    except Exception as exc:
        operations.audit(actor, "openai-key.update", "application-key", "validation-failed")
        raise HTTPException(status_code=400, detail="The OpenAI key could not be validated") from exc
    operations.write_runtime_secret("OPENAI_API_KEY", candidate)
    replace_openai_api_key(candidate)
    operations.audit(actor, "openai-key.update", "application-key", "success")
    return {"updated": True, "runtimeConfig": str(RUNTIME_CONFIG_PATH.name)}
