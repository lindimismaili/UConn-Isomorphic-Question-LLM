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
from dataclasses import replace

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from backend.src.api import state
from backend.src.api.batch import router as batch_router
from backend.src.api.config import router as config_router
from backend.src.api.eval_batch import router as eval_batch_router
from backend.src.api.files import router as files_router
from backend.src.api.health import router as health_router
from backend.src.api.professor_comparison import router as professor_comparison_router
from backend.src.api.workflows import router as workflows_router
from backend.src.auth import AccessBoundaryConfig, AccessBoundaryMiddleware
from backend.src.paths import DIST_DIR
from backend.src.registry import api_description as _api_description
from backend.src.registry import api_title as _api_title


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Run registry/artifact pruning on an interval instead of on every request."""
    state.reconcile_persisted_state()
    prune_task = asyncio.create_task(state.pruning_loop())
    try:
        yield
    finally:
        prune_task.cancel()
        try:
            await prune_task
        except asyncio.CancelledError:
            pass
        await state.shutdown_runtime_tasks()


def create_app(*, access_config: AccessBoundaryConfig | None = None) -> FastAPI:
    """Build the API with an optional token boundary for production deployments."""
    configured_origins = tuple(dict.fromkeys(state._configured_allowed_origins()))
    if access_config is None:
        config = AccessBoundaryConfig.from_env(trusted_origins=configured_origins)
    elif access_config.trusted_origins:
        config = access_config
    else:
        config = replace(access_config, trusted_origins=configured_origins)
    docs_url = None if config.auth_required else "/docs"
    redoc_url = None if config.auth_required else "/redoc"
    openapi_url = None if config.auth_required else "/openapi.json"

    application = FastAPI(
        title=_api_title(),
        description=_api_description(),
        version="1.0.0",
        lifespan=lifespan,
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
    )
    application.state.auth_required = config.auth_required

    application.add_middleware(AccessBoundaryMiddleware, config=config)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(config.trusted_origins),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Domain routers (registered before the catch-all static mount).
    application.include_router(health_router)
    application.include_router(config_router)
    application.include_router(files_router)
    application.include_router(workflows_router)
    application.include_router(batch_router)
    application.include_router(eval_batch_router)
    application.include_router(professor_comparison_router)

    # Mount static files last so API routes win.
    if DIST_DIR.is_dir() and (DIST_DIR / "index.html").is_file():
        application.mount("/", StaticFiles(directory=str(DIST_DIR), html=True), name="static")
    else:

        @application.get("/", include_in_schema=False)
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
    <p>Dev UI: run <code>cd site &amp;&amp; npm -C frontend run dev:ui</code> (then open the Vite URL).</p>
    <p>Prod UI: run <code>cd site &amp;&amp; npm -C frontend run build</code>, then refresh this page.</p>
    <p>API health: <a href="/api/health">/api/health</a></p>
  </body>
</html>
                """.strip()
            )

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)
