"""
Run the Isomorphic Question Generator Server

Usage:
    python -m backend.src.run [--port PORT] [--host HOST]
    
Options:
    --port PORT     Port to run server on (default: 8000)
    --host HOST     Host to bind to (default: 0.0.0.0)
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import uvicorn


def ensure_runtime_dirs() -> None:
    """Ensure runtime directories exist before server startup."""
    backend_root = Path(__file__).resolve().parent.parent
    (backend_root / "uploads").mkdir(parents=True, exist_ok=True)
    (backend_root / "outputs").mkdir(parents=True, exist_ok=True)


def _latest_mtime(path: Path) -> float:
    if not path.exists():
        return 0.0
    if path.is_file():
        return path.stat().st_mtime

    latest = 0.0
    for child in path.rglob("*"):
        if child.is_file():
            latest = max(latest, child.stat().st_mtime)
    return latest


def _resolve_npm_command() -> str:
    if os.name == "nt":
        return shutil.which("npm.cmd") or shutil.which("npm") or "npm.cmd"
    return shutil.which("npm") or "npm"


def ensure_dist_built() -> None:
    """Build the frontend if dist/ is missing or older than its inputs."""
    repo_root = Path(__file__).resolve().parents[2]
    frontend_dir = repo_root / "frontend"
    dist_dir = repo_root / "dist"
    dist_index = dist_dir / "index.html"

    build_inputs = [
        frontend_dir / "src",
        frontend_dir / "public",
        frontend_dir / "index.html",
        frontend_dir / "package.json",
        frontend_dir / "package-lock.json",
        frontend_dir / "vite.config.js",
        repo_root / "backend" / "data" / "agents.json",
    ]

    latest_input_mtime = max(_latest_mtime(path) for path in build_inputs)
    latest_dist_mtime = _latest_mtime(dist_dir)

    needs_build = not dist_index.exists() or latest_dist_mtime < latest_input_mtime
    if not needs_build:
        return

    npm_command = _resolve_npm_command()
    reason = "missing dist/index.html" if not dist_index.exists() else "frontend sources are newer than dist/"
    print(f"Frontend build required: {reason}")
    subprocess.run(
        [npm_command, "-C", str(frontend_dir), "run", "build"],
        cwd=repo_root,
        check=True,
    )


def ensure_import_path() -> None:
    """Ensure repo root is importable when this file is executed as a script."""
    repo_root = Path(__file__).resolve().parents[2]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def main():
    parser = argparse.ArgumentParser(
        description="Run the Isomorphic Question Generator Server"
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=8000,
        help="Port to run server on (default: 8000)"
    )
    parser.add_argument(
        "--host", "-H",
        type=str,
        default="0.0.0.0",
        help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--reload", "-r",
        action="store_true",
        help="Enable auto-reload for development"
    )
    
    args = parser.parse_args()
    ensure_import_path()
    ensure_runtime_dirs()
    ensure_dist_built()

    banner = (
        "\n"
        "============================================================\n"
        "  Isomorphic Question Generator Server\n"
        "------------------------------------------------------------\n"
        f"  Starting server at: http://{args.host}:{args.port}\n"
        f"  Web Interface:       http://localhost:{args.port}\n"
        "\n"
        "  Press Ctrl+C to stop the server\n"
        "============================================================\n"
    )

    try:
        print(banner)
    except UnicodeEncodeError:
        # Some Windows terminals still default to legacy encodings; keep startup robust.
        safe_banner = banner.encode("utf-8", errors="replace").decode("ascii", errors="replace")
        print(safe_banner)
    
    uvicorn.run(
        "backend.src.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
