"""
Run the Isomorphic Question Generator Server

Usage:
    python src/run.py [--port PORT] [--host HOST]
    
Options:
    --port PORT     Port to run server on (default: 8000)
    --host HOST     Host to bind to (default: 0.0.0.0)
"""

import argparse
import uvicorn


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
    
    print(f"""
╔══════════════════════════════════════════════════════════╗
║       Isomorphic Question Generator Server               ║
╠══════════════════════════════════════════════════════════╣
║  Starting server at: http://{args.host}:{args.port}         
║  Web Interface: http://localhost:{args.port}               
║                                                          ║
║  Press Ctrl+C to stop the server                         ║
╚══════════════════════════════════════════════════════════╝
""")
    
    uvicorn.run(
        "src.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
