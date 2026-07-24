"""FastAPI application factory for the Isomorphic Question Generator.

Route handlers live in the domain routers under ``backend/src/api`` (health,
config, files, workflows, batch). Shared state, configuration, and the
workflow-runner wrappers live in ``backend/src/api/state``. This module only
wires those together, mounts the built frontend, and exposes ``app`` for
``uvicorn backend.src.server:app``.
"""

# Allow `python backend/src/server.py` (script mode) to use absolute backend.src
# imports without a try/except dance. In `python -m backend.src.server` and
# `uvicorn backend.src.server:app` modes the insert is a harmless no-op.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.src.api import state
from backend.src.api.admin import router as admin_router
from backend.src.api.batch import router as batch_router
from backend.src.api.config import router as config_router
from backend.src.api.eval_batch import router as eval_batch_router
from backend.src.api.files import router as files_router
from backend.src.api.health import router as health_router
from backend.src.api.professor_comparison import router as professor_comparison_router
from backend.src.api.workflows import router as workflows_router
from backend.src.operations import (
    CODE_COST,
    CODE_SERVER,
    fetch_openai_cost_summary,
    fetch_openai_status,
    operations,
    send_incident_email,
)
from backend.src.paths import DIST_DIR
from backend.src.registry import api_description as _api_description
from backend.src.registry import api_title as _api_title


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run registry/artifact pruning on an interval instead of on every request."""
    prune_task = asyncio.create_task(state.pruning_loop())
    async def operations_monitor() -> None:
        while True:
            await asyncio.sleep(max(30, int(os.getenv("OPERATIONS_MONITOR_SECONDS", "60"))))
            status = await asyncio.to_thread(fetch_openai_status, state.OPENAI_STATUS_TIMEOUT_SECONDS)
            if status.get("openai_down"):
                incident = operations.open_incident(
                    code=CODE_SERVER,
                    title="OpenAI service outage",
                    detail=str(status.get("description") or "OpenAI reports a major or critical outage."),
                )
                operations.set_maintenance(active=True, code=CODE_SERVER, message=incident["detail"])
                if incident.get("new"):
                    await asyncio.to_thread(send_incident_email, incident)
            cost = await asyncio.to_thread(fetch_openai_cost_summary)
            threshold = operations.get_setting("cost_warning_dollars", None)
            if cost.get("available") and isinstance(threshold, (int, float)) and cost.get("amount", 0) >= threshold:
                incident = operations.open_incident(
                    code=CODE_COST,
                    title="OpenAI cost warning threshold reached",
                    detail=f"Current daily organization cost is {cost['amount']} {cost['currency']} (threshold {threshold}).",
                    severity="warning",
                )
                if incident.get("new"):
                    await asyncio.to_thread(send_incident_email, incident)
    monitor_task = asyncio.create_task(operations_monitor())
    try:
        yield
    finally:
        prune_task.cancel()
        monitor_task.cancel()
        try:
            await prune_task
        except asyncio.CancelledError:
            pass
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass


app = FastAPI(
    title=_api_title(),
    description=_api_description(),
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=state._configured_allowed_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Domain routers (registered before the catch-all static mount).
app.include_router(health_router)
app.include_router(admin_router)
app.include_router(config_router)
app.include_router(files_router)
app.include_router(workflows_router)
app.include_router(batch_router)
app.include_router(eval_batch_router)
app.include_router(professor_comparison_router)


# Mount static files last so API routes win.
if DIST_DIR.is_dir() and (DIST_DIR / "index.html").is_file():
    app.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="static")
else:
    @app.get("/", include_in_schema=False)
    async def root():
        """Return a diagnostic landing page when the frontend has not been built."""
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
    <p>Dev UI: run <code>npm -C frontend run dev:ui</code> (then open the Vite URL).</p>
    <p>Prod UI: run <code>npm -C frontend run build</code>, then refresh this page.</p>
    <p>API health: <a href="/api/health">/api/health</a></p>
  </body>
</html>
            """.strip()
        )


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
