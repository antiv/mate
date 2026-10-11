---
title: "Code executor setup"
summary: What the code executor tool runs, where, and how to contain it.
audience: dev
order: 220
covers:
  - shared/utils/tools/code_executor_tools.py
  - shared/utils/tools/tool_factory.py
  - templates/dashboard/modals/config_modals.html
  - static/js/agent-tools.js
---

# Code Executor Tool

## Overview

The Code Executor tool allows agents to write and run Python scripts (and shell commands) at runtime. This is useful for agents that need to perform calculations, data transformations, generate files, or execute any logic that is easier expressed as code than natural language.

## Tools Provided

When enabled, the agent receives two tools:

### `execute_python_code`

Writes Python source code to a temporary file (`script.py`) in a temporary directory (`mate_exec_*`) and executes it via subprocess.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `code` | `str` | *required* | Python source code to execute |
| `timeout_seconds` | `int` | 30 | Max execution time (capped at 120s) |

**Returns:**
```json
{
  "status": "success",
  "stdout": "Hello world\n",
  "stderr": "",
  "exit_code": 0,
  "timed_out": false
}
```

On validation failure or exceptions, it returns an error shape without `stdout`/`stderr`:
```json
{
  "status": "error",
  "error_message": "No code provided."
}
```

### `execute_shell_command`

Runs an arbitrary shell command in the server's working directory.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `command` | `str` | *required* | Shell command to execute |
| `timeout_seconds` | `int` | 30 | Max execution time (capped at 120s) |

**Returns:** Same structure as `execute_python_code`. On error (e.g. empty command or exception), returns `{"status": "error", "error_message": ...}`.

## Configuration

### Enable via Dashboard

1. Open the agent in the Dashboard
2. Click **Tool Configuration**
3. Check the **Code Executor** checkbox
4. Click **Save**

*Note:* Saving Tool Configuration in the dashboard writes `code_executor = true` (`static/js/agent-tools.js`), which resets any custom timeout configured via raw JSON back to the 30-second default.

### Enable via `tool_config` JSON

```json
{
  "code_executor": true
}
```

With a custom default timeout (seconds):

```json
{
  "code_executor": {
    "timeout": 60
  }
}
```

### Enable via SQL

```sql
UPDATE agents_config
SET tool_config = '{"code_executor": true}'
WHERE name = 'my_agent';
```

## Limits & Safety

| Limit | Value |
|---|---|
| Max timeout | 120 seconds |
| Default timeout | 30 seconds |
| Max output size | 50,000 characters (stdout/stderr each) |
| Execution directory | Temp directory (`mate_exec_*`) for Python; server working directory for shell |
| Python binary | `python` (the first `python` on `PATH`, not necessarily `sys.executable`) |

Scripts have access to whatever packages are installed in the server's Python environment. They inherit the server's environment variables (minus any sandboxing you add at the infrastructure level).

### Never on a public agent

This tool is not a sandbox. Enabling it on an agent grants host code execution to anyone who can prompt that agent, running as the server user with the server's environment and full filesystem and network access.

MATE enforces one strict protection against exposure: **an agent that has a widget API key does not receive the code executor tools.** A widget key makes the agent promptable by anonymous visitors on an embedding site, which would turn the executor into remote shell access for the public internet. The refusal is logged at load time, and the dashboard modal (`templates/dashboard/modals/config_modals.html` / `static/js/agent-tools.js`) warns when you tick the box on such an agent.

A key counts whether or not it is currently active. Reactivating an inactive key is a dashboard toggle that does not touch the agent configuration, so an inactive key represents exposure waiting to happen. To use the executor on such an agent, delete the widget key.

| Variable | Effect |
|---|---|
| `MATE_ALLOW_CODE_EXECUTOR_ON_WIDGET` | `1`, `true`, or `yes` skips the check. Only set this if the widget key is not reachable by untrusted users. |

The check fails closed: if it cannot reach the database to determine exposure, the tools are refused rather than granted.

## Example Agent Instruction

```
You are a data analysis agent. When users ask you to compute, transform, 
or analyze data, write a Python script using the execute_python_code tool 
and return the results. Use pandas, numpy, or standard library as needed.
Always print your final results to stdout.
```

## Architecture Notes

- For Python executions, a temp directory (`mate_exec_*`) is created and `script.py` is written to it. Cleanup removes `script.py` and attempts `os.rmdir(tmp_dir)`. If the script wrote additional files into that directory, `rmdir` fails silently and the temporary folder remains on disk.
- Shell commands run directly with `shell=True` in the server's working directory without a temporary folder.
- Subprocesses inherit `os.environ` with `PYTHONDONTWRITEBYTECODE=1`.
- Timed-out processes trigger a timeout error. For `execute_shell_command`, only the spawned `/bin/sh` shell process is killed; child processes it launched can continue running in the background.
- For production deployments, consider running the executor in a restricted container or dedicated sandboxed runtime.

