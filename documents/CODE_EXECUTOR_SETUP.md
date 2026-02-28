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
