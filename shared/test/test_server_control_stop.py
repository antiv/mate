"""
Stopping the agent server leaves every other process alone, another installation's
agent server too.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.utils.server_control_service import ServerControlService

WAIT = "import time\ntime.sleep(120)\n"


class TestStopOwnAgentServer(unittest.TestCase):

    def setUp(self) -> None:
        self.roots = [os.path.realpath(tempfile.mkdtemp()) for _ in range(2)]
        for root in self.roots:
            self.addCleanup(shutil.rmtree, root, True)
            for name in ("adk_main.py", "langgraph_main.py", "other.py"):
                Path(root, name).write_text(WAIT)

    def _run(self, args: List[str], port: Optional[str] = "8501", cwd: Optional[str] = None) -> subprocess.Popen:
        env = {k: v for k, v in os.environ.items() if k != "PORT"}
        if port:
            env["PORT"] = port
        process = subprocess.Popen([sys.executable, *args], env=env, cwd=cwd)
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        return process

    def _stop(self, root: str) -> Any:
        service = ServerControlService(adk_host="127.0.0.1", adk_port=8501,
                                       session_service_uri="sqlite:///sessions.db")
        service.project_root = Path(root)
        time.sleep(0.5)  # the processes have their command lines
        with patch.object(service, "get_adk_status", return_value={"status": "stopped"}), \
                patch("shared.utils.server_control_service.time.sleep"):
            return service.stop_adk_server()

    def _gone(self, process: subprocess.Popen) -> bool:
        try:
            process.wait(timeout=5)
            return True
        except subprocess.TimeoutExpired:
            return False

    def test_stops_only_its_own(self) -> None:
        mine, theirs = self.roots
        own = [self._run([os.path.join(mine, "adk_main.py"), "--a2a"]),
               self._run([os.path.join(mine, "langgraph_main.py")]),
               self._run(["adk_main.py"], cwd=mine),
               self._run([os.path.join(mine, "adk_main.py")], port=None)]
        others = [self._run([os.path.join(theirs, "adk_main.py")]),
                  self._run(["adk_main.py"], cwd=theirs),
                  self._run([os.path.join(mine, "adk_main.py")], port="8502"),
                  self._run([os.path.join(theirs, "other.py"), "adk", "web"]),
                  self._run([os.path.join(mine, "other.py"), os.path.join(mine, "adk_main.py")])]
        result = self._stop(mine)
        self.assertTrue(result["success"], result)
        self.assertIn("4 process", result["message"])
        self.assertTrue(all(self._gone(p) for p in own))
        self.assertEqual([p.poll() for p in others], [None] * len(others))

    def test_nothing_of_its_own_to_stop(self) -> None:
        other = self._run([os.path.join(self.roots[1], "adk_main.py")])
        self.assertFalse(self._stop(self.roots[0])["success"])
        self.assertIsNone(other.poll())


if __name__ == "__main__":
    unittest.main()
