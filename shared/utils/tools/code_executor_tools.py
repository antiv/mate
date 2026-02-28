"""
Code executor tools for agents.
Allows agents to write and execute Python scripts in a sandboxed subprocess.
"""

import logging
import os
import subprocess
import tempfile
import textwrap
from typing import Dict, Any, List, Optional

from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 50_000
DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120


def create_code_executor_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """
    Create code executor tools from agent config.
    tool_config example: {"code_executor": true}
    or with options:     {"code_executor": {"timeout": 60}}
    """
    tool_config = config.get("tool_config")
    timeout = DEFAULT_TIMEOUT_SECONDS

    if isinstance(tool_config, str):
        import json
        try:
            tool_config = json.loads(tool_config)
        except Exception:
            tool_config = {}

    if isinstance(tool_config, dict):
        ce_cfg = tool_config.get("code_executor", {})
        if isinstance(ce_cfg, dict):
            timeout = min(ce_cfg.get("timeout", DEFAULT_TIMEOUT_SECONDS), MAX_TIMEOUT_SECONDS)

    def execute_python_code(
        code: str,
        timeout_seconds: Optional[int] = None,
        tool_context: ToolContext = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Execute a Python script and return its stdout, stderr, and exit code.

        Args:
            code: Python source code to execute.
            timeout_seconds: Max execution time in seconds (default 30, max 120).

        Returns:
            Dict with keys: status, stdout, stderr, exit_code, timed_out.
        """
        effective_timeout = min(timeout_seconds or timeout, MAX_TIMEOUT_SECONDS)

        if not code or not code.strip():
            return {"status": "error", "error_message": "No code provided."}

        code = textwrap.dedent(code)

        tmp_dir = tempfile.mkdtemp(prefix="mate_exec_")
        script_path = os.path.join(tmp_dir, "script.py")

        try:
            with open(script_path, "w") as f:
                f.write(code)

            result = subprocess.run(
                ["python", script_path],
                capture_output=True,
                text=True,
                timeout=effective_timeout,
                cwd=tmp_dir,
                env={
                    **os.environ,
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )

            stdout = result.stdout[:MAX_OUTPUT_CHARS] if result.stdout else ""
            stderr = result.stderr[:MAX_OUTPUT_CHARS] if result.stderr else ""

            return {
                "status": "success",
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": result.returncode,
                "timed_out": False,
            }

        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "stdout": "",
                "stderr": f"Execution timed out after {effective_timeout} seconds.",
                "exit_code": -1,
                "timed_out": True,
            }
        except Exception as e:
            logger.error(f"Code execution failed: {e}")
            return {
                "status": "error",
                "error_message": str(e),
                "exit_code": -1,
                "timed_out": False,
            }
        finally:
            try:
                os.unlink(script_path)
                os.rmdir(tmp_dir)
            except OSError:
                pass

    def execute_shell_command(
        command: str,
        timeout_seconds: Optional[int] = None,
        tool_context: ToolContext = None,
        **kwargs,
    ) -> Dict[str, Any]:
        """Execute a shell command and return its stdout, stderr, and exit code.

        Args:
            command: Shell command to execute.
            timeout_seconds: Max execution time in seconds (default 30, max 120).

        Returns:
            Dict with keys: status, stdout, stderr, exit_code, timed_out.
        """
        effective_timeout = min(timeout_seconds or timeout, MAX_TIMEOUT_SECONDS)

        if not command or not command.strip():
            return {"status": "error", "error_message": "No command provided."}

        try:
            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )

            stdout = result.stdout[:MAX_OUTPUT_CHARS] if result.stdout else ""
            stderr = result.stderr[:MAX_OUTPUT_CHARS] if result.stderr else ""

            return {
                "status": "success",
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": result.returncode,
                "timed_out": False,
            }

        except subprocess.TimeoutExpired:
            return {
                "status": "error",
                "stdout": "",
                "stderr": f"Command timed out after {effective_timeout} seconds.",
                "exit_code": -1,
                "timed_out": True,
            }
        except Exception as e:
            logger.error(f"Shell command execution failed: {e}")
            return {
                "status": "error",
                "error_message": str(e),
                "exit_code": -1,
                "timed_out": False,
            }

    return [execute_python_code, execute_shell_command]
