"""A MATE of its own for the browser tests: a copy of the working tree, so that the agent
folders it writes don't land in agents/, an empty SQLite database, free ports and the
stand-in model. Nothing of the developer's .env, data or running server is used."""

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
USERNAME, PASSWORD = "admin", "mate"  # the development default, on a database made for the test
MODEL = "openrouter/stand-in/model"
# What a server of the test must not take from the shell it is started in
KEPT_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "VIRTUAL_ENV", "PYTHONPATH", "SYSTEMROOT")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class Mate:
    def __init__(self) -> None:
        self.dir = tempfile.mkdtemp(prefix="mate-browser-")
        self.tree = os.path.join(self.dir, "mate")
        self.ports = {"auth": free_port(), "agents": free_port(), "model": free_port()}
        self.url = f"http://127.0.0.1:{self.ports['auth']}"
        self._processes: List[subprocess.Popen] = []

    def start(self) -> None:
        self._copy_tree()
        model = f"http://127.0.0.1:{self.ports['model']}/v1"
        env = {k: v for k, v in os.environ.items() if k in KEPT_ENV}
        env.update({
            "PYTHON_DOTENV_DISABLED": "1", "DB_TYPE": "sqlite", "DB_PATH": os.path.join(self.dir, "mate.db"),
            "ADK_HOST": "127.0.0.1", "ADK_PORT": str(self.ports["agents"]),
            "AUTH_USERNAME": USERNAME, "AUTH_PASSWORD": PASSWORD, "SECRET_KEY": "browser-tests",
            "CSP_MODE": "enforce", "ALLOWED_ORIGINS": self.url, "LITELLM_LOCAL_MODEL_COST_MAP": "True",
            "OPENROUTER_API_KEY": "stand-in", "OPENROUTER_BASE_URL": model, "OPENROUTER_API_BASE": model,
            "GEMINI_MODEL": MODEL, "TITLE_GEN_MODEL": MODEL,
        })
        self._run(env, "model")
        self._run(env, "auth")
        self._wait(self.url + "/login", "auth")
        self._fill()
        # After the agents are in the database: the agent server lists folders at its start
        self._run(env, "agents")
        self._wait(f"http://127.0.0.1:{self.ports['agents']}/list-apps", "agents")

    def stop(self) -> None:
        for process in self._processes:
            process.kill()
            process.wait()
        shutil.rmtree(self.dir, ignore_errors=True)

    def log(self, name: str) -> str:
        with open(os.path.join(self.dir, f"{name}.log"), encoding="utf-8", errors="replace") as f:
            return f.read()

    def _copy_tree(self) -> None:
        names = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=ROOT,
                               check=True, capture_output=True, text=True).stdout.split("\0")
        for name in names:
            source = os.path.join(ROOT, name)
            if not name or name.startswith("documents/") or not os.path.isfile(source):
                continue
            target = os.path.join(self.tree, name)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.copy2(source, target)

    def _run(self, env: Dict[str, str], name: str) -> None:
        with open(os.path.join(self.dir, f"{name}.log"), "w", encoding="utf-8") as log:
            self._processes.append(subprocess.Popen(
                [sys.executable, "shared/test/browser/launch.py", name, str(self.ports[name])], cwd=self.tree, env=env,
                stdout=log, stderr=subprocess.STDOUT))

    def _wait(self, url: str, name: str, seconds: int = 120) -> None:
        deadline = time.time() + seconds
        while time.time() < deadline:
            if self._processes[-1].poll() is not None:
                break
            try:
                if httpx.get(url, timeout=5).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"{name} did not start:\n{self.log(name)[-3000:]}")

    def call(self, method: str, path: str, body: Optional[Dict[str, Any]] = None, **kwargs: Any) -> Any:
        response = httpx.request(method, self.url + path, json=body, auth=(USERNAME, PASSWORD),
                                 headers={"Origin": self.url}, timeout=60, **kwargs)
        response.raise_for_status()
        return response.json()

    def _fill(self) -> None:
        """Long names and full tables: what breaks a layout."""
        project = self.call("POST", "/dashboard/api/templates/import", {
            "template_id": "customer-support", "project_name": "Customer Support of a long-named company"})
        self.project_id, self.agent = project["project_id"], project["root_agent_name"]
        self.call("POST", "/dashboard/api/triggers", {
            "name": "Summary of the week for everyone", "agent_name": self.agent,
            "project_id": self.project_id, "trigger_type": "cron", "cron_expression": "0 7 * * 1",
            "prompt": "Summarize the week.", "output_type": "memory_block",
            "output_config": {"label": "weekly_summary"}})
        from sqlalchemy import create_engine, text
        engine = create_engine("sqlite:///" + os.path.join(self.dir, "mate.db"))
        with engine.begin() as db:
            db.execute(text("UPDATE agents_config SET model_name = :m"), {"m": MODEL})
            for n in range(60):
                db.execute(text(
                    "INSERT INTO token_usage_logs (request_id, session_id, user_id, agent_name, model_name, "
                    "prompt_tokens, response_tokens, status, timestamp, cost_usd, project_id, workspace_id) "
                    "VALUES (:r, :s, :u, :a, :m, 1200, 340, 'SUCCESS', datetime('now', :t), 0.0021, :p, 1)"), {
                    "r": f"e-1b7eb8a4-b960-43de-b6{n:02d}", "s": f"session-{n % 7}",
                    "u": f"someone.with.a.long.name{n % 9}@example.com", "a": self.agent,
                    "m": "openrouter/anthropic/claude-sonnet-4.5", "t": f"-{n * 5} hours", "p": self.project_id})
        engine.dispose()
