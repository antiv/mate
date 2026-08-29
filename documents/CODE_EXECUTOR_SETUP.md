# Code Executor Tool

## Overview

The Code Executor tool allows agents to write and run Python scripts (and shell commands) at runtime. This is useful for agents that need to perform calculations, data transformations, generate files, or execute any logic that is easier expressed as code than natural language.

## Tools Provided

When enabled, the agent receives two tools:

### `execute_python_code`

Writes Python source code to a temporary file and executes it via subprocess.

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

### `execute_shell_command`

Runs an arbitrary shell command.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `command` | `str` | *required* | Shell command to execute |
| `timeout_seconds` | `int` | 30 | Max execution time (capped at 120s) |

**Returns:** Same structure as `execute_python_code`.

## Configuration

### Enable via Dashboard

1. Open the agent in the Dashboard
2. Click **Tool Configuration**
3. Check the **Code Executor** checkbox
4. Click **Save**

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
|-------|-------|
| Max timeout | 120 seconds |
| Default timeout | 30 seconds |
| Max output size | 50,000 characters (stdout/stderr each) |
| Execution directory | Isolated temp directory per run (cleaned up after) |
| Python binary | System `python` (same as the server process) |

Scripts have access to whatever packages are installed in the server's Python environment. They inherit the server's environment variables (minus any sandboxing you add at the infrastructure level).

### Never on a public agent

This is not a sandbox. Enabling it on an agent grants code execution on the host to anyone who
can prompt that agent, as the server user, with the server's environment and full filesystem and
network access.

MATE enforces one case of this: **an agent that has a widget API key does not receive the code
executor tools.** A widget key makes the agent promptable by anonymous visitors on the embedding
site, which would turn the executor into remote shell access for the public internet. The refusal
is logged at load time, and the dashboard warns when you tick the box on such an agent.

A key counts whether or not it is currently active — reactivating one is a dashboard toggle that
never touches the agent, so an inactive key is exposure waiting to happen rather than the absence
of it. To use the executor on such an agent, delete the widget key.

| Variable | Effect |
|----------|--------|
| `MATE_ALLOW_CODE_EXECUTOR_ON_WIDGET` | `true` skips the check. Only set this if the widget key is not reachable by untrusted users. |

The check fails closed: if it cannot reach the database to determine exposure, the tools are
refused rather than granted.

## Example Agent Instruction

```
You are a data analysis agent. When users ask you to compute, transform, 
or analyze data, write a Python script using the execute_python_code tool 
and return the results. Use pandas, numpy, or standard library as needed.
Always print your final results to stdout.
```

## Architecture Notes

- Each execution creates a fresh temp directory, writes `script.py`, runs it, and cleans up.
- The subprocess inherits `os.environ` with `PYTHONDONTWRITEBYTECODE=1`.
- Timed-out processes are killed by Python's `subprocess.run(timeout=...)`.
- No persistent state between executions — each call is independent.
- For production deployments, consider running the executor in a restricted container or using a dedicated sandboxed runtime.
