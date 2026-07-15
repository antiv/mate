"""
LangGraph agent runtime entrypoint (AGENT_FRAMEWORK=langgraph).

Mirrors adk_main.py's launch contract: same CLI args, PORT env var, and parent
process monitoring — but serves agents via LangGraph while emulating the ADK
HTTP/SSE wire protocol so the auth server and frontends work unchanged.
"""

import os
import argparse
import threading
from dotenv import load_dotenv
import uvicorn

# Load environment variables from .env file
load_dotenv()

from shared.utils.logging_config import configure_logging
configure_logging()

parser = argparse.ArgumentParser()
parser.add_argument("--session-db-url", default="sqlite:///session_db.db")
parser.add_argument("--host", default="0.0.0.0")
parser.add_argument("--a2a", action="store_true", help="Accepted for launch-contract parity; A2A is not supported by the langgraph runtime")
args = parser.parse_args()
SESSION_DB_URL = args.session_db_url
HOST = args.host

if args.a2a:
    print("⚠️  A2A is not supported by the langgraph runtime; --a2a ignored")

ALLOWED_ORIGINS = ["http://localhost", "http://localhost:8000", "*"]

from shared.utils.langgraph.api import create_app

app = create_app(allow_origins=ALLOWED_ORIGINS)

if __name__ == "__main__":
    # Terminate if the parent auth server dies (same behavior as adk_main.py)
    def monitor_parent():
        import time
        import sys
        import signal

        parent_pid = os.getppid()
        if parent_pid > 1:
            while True:
                time.sleep(2)
                if os.getppid() != parent_pid:
                    print(f"⚠️ Parent process {parent_pid} died. Terminating LangGraph server...", flush=True)
                    os.kill(os.getpid(), signal.SIGTERM)
                    time.sleep(5)
                    sys.exit(0)

    monitor_thread = threading.Thread(target=monitor_parent, daemon=True)
    monitor_thread.start()

    port = int(os.getenv("PORT", 8000))
    print(f"🚀 Starting LangGraph server on {HOST}:{port}")
    print(f"🚀 Session DB URL: {SESSION_DB_URL}")

    log_level = os.getenv("LOG_LEVEL", "info").lower()
    if log_level not in ["critical", "error", "warning", "info", "debug", "trace"]:
        log_level = "info"
    access_log = os.getenv("ACCESS_LOG", "true").lower() != "false"
    uvicorn.run(app, host=HOST, port=port, log_level=log_level, access_log=access_log)
