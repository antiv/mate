"""
The agent server is started with the interpreter that runs the auth server,
not with whatever `python` is first on PATH.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from shared.utils.server_control_service import ServerControlService


class TestAgentServerInterpreter(unittest.TestCase):

    def _start(self) -> list:
        service = ServerControlService(
            adk_host="127.0.0.1", adk_port=8001, session_service_uri="sqlite:///sessions.db"
        )
        process = MagicMock()
        process.poll.return_value = 1  # exits at once, so start_adk_server returns without polling HTTP
        process.returncode = 1
        with patch.object(service, "get_adk_status", return_value={"status": "stopped"}), \
                patch.object(service, "_initialize_agent_folders"), \
                patch("shared.utils.server_control_service.subprocess.Popen", return_value=process) as popen, \
                patch("shared.utils.server_control_service.time.sleep"):
            service.start_adk_server()
        return popen.call_args.args[0]

    def test_child_runs_under_the_current_interpreter(self):
        self.assertEqual(self._start()[0], sys.executable)

    def test_python_on_path_is_not_consulted(self):
        with patch.dict(os.environ, {"PATH": ""}):
            self.assertEqual(self._start()[0], sys.executable)

    def test_runtime_script_follows_the_configured_framework(self):
        with patch.dict(os.environ, {"AGENT_FRAMEWORK": "langgraph"}):
            self.assertEqual(os.path.basename(self._start()[1]), "langgraph_main.py")
        with patch.dict(os.environ, {"AGENT_FRAMEWORK": "adk"}):
            self.assertEqual(os.path.basename(self._start()[1]), "adk_main.py")


if __name__ == "__main__":
    unittest.main()
