"""
Run the Isomorphic Question Generator Server

Usage:
    python -m backend.src.run [--port PORT] [--host HOST]
    
Options:
    --port PORT     Port to run server on (default: 8000)
    --host HOST     Host to bind to (default: 0.0.0.0)
"""

import argparse
from pathlib import Path

import uvicorn


def ensure_runtime_dirs() -> None:
    """Ensure runtime directories exist before server startup."""
    backend_root = Path(__file__).resolve().parent.parent
    (backend_root / "uploads").mkdir(parents=True, exist_ok=True)
    (backend_root / "outputs").mkdir(parents=True, exist_ok=True)


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
    ensure_runtime_dirs()

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
