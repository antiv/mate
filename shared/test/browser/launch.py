"""Starts MATE for the browser tests: `python shared/test/browser/launch.py auth|agents|model <port>`.

As a script, not a module: importing shared.test gives the process a database of its own.

Not `python auth_server.py`: that listens on port 8000 and, when it stops, stops every
process on the machine whose command line has adk_main.py in it, a developer's own
server too.
"""

import os
import runpy
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def main() -> None:
    kind, port = sys.argv[1], sys.argv[2]
    sys.path[0] = ROOT  # not this directory
    import uvicorn
    if kind == "model":
        stand_in = runpy.run_path(os.path.join(os.path.dirname(os.path.abspath(__file__)), "stand_in_model.py"))
        uvicorn.run(stand_in["app"], host="127.0.0.1", port=int(port), log_level="warning")
        return
    from shared.utils import model_pricing
    from shared.utils.server_control_service import ServerControlService
    model_pricing._fetch_openrouter = lambda: None  # no network but the CDNs
    if kind == "agents":
        ServerControlService()._initialize_agent_folders()  # as the auth server does before it starts one
        sys.path.insert(0, os.path.join(ROOT, "agents"))
        os.environ["PORT"] = port
        script = os.path.join(ROOT, "adk_main.py")
        sys.argv = [script, "--host", "127.0.0.1", "--session-db-url", "sqlite:///" + os.environ["DB_PATH"]]
        runpy.run_path(script, run_name="__main__")
        return
    ServerControlService.stop_adk_server = lambda self: {"success": True, "message": "left alone"}
    import auth_server
    uvicorn.run(auth_server.app, host="127.0.0.1", port=int(port), log_level="warning")


if __name__ == "__main__":
    main()
