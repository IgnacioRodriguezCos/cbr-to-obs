#!/usr/bin/env python3
"""Launcher for the CBR-to-OBS Migration local web app.

Installs dependencies if needed, starts the FastAPI server,
and opens the browser automatically.

Usage:
    python run.py
    python run.py --no-browser   # Don't open browser
    python run.py --port 9090    # Custom port
"""

import os
import sys
import subprocess
import argparse


def check_and_install_deps():
    """Check if required packages are installed, install if not."""
    required = ["fastapi", "uvicorn", "requests"]
    missing = []
    for pkg in required:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)

    if missing:
        print(f"Installing dependencies: {', '.join(missing)}")
        req_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "local", "requirements.txt")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file, "--quiet"])
        print("Dependencies installed.")


def main():
    parser = argparse.ArgumentParser(description="CBR-to-OBS Migration Local App")
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser")
    parser.add_argument("--port", type=int, default=8080, help="Server port (default: 8080)")
    parser.add_argument("--host", default="127.0.0.1", help="Server host (default: 127.0.0.1)")
    args = parser.parse_args()

    check_and_install_deps()

    project_root = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, project_root)

    if not args.no_browser:
        import threading
        import webbrowser
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()

    print(f"\n{'='*50}")
    print(f"  CBR-to-OBS Migration Tool")
    print(f"  Server: http://localhost:{args.port}")
    print(f"  Press Ctrl+C to stop")
    print(f"{'='*50}\n")

    import uvicorn
    uvicorn.run("local.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
