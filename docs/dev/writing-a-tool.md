---
title: Writing a tool
summary: Add a built-in tool: the module, its registration in the tool factory, the dashboard switch, tests and docs.
audience: dev
order: 30
covers:
  - shared/utils/tools/tool_factory.py
  - shared/utils/tools/custom_tools.py
  - shared/utils/tools/async_tool_adapter.py
  - shared/utils/tools/README.md
---

# Writing a tool

A tool is a Python function the model can call. MATE's built-in tools are grouped in
modules under `shared/utils/tools/`, and an agent enables a group with a key in its
`tool_config` JSON. This page adds a new group.

If the capability already exists as an MCP server, connect that instead; nothing
needs to be written. See [Tools and MCP servers](../user/tools-and-mcp.md).

## 1. The module

Create `shared/utils/tools/weather_tools.py` with one factory function that returns
the tool functions:

```python
"""Weather tools: look up a forecast."""

import json
from typing import Any, Dict, List

from google.adk.tools.tool_context import ToolContext


def create_weather_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """Build the weather tools if tool_config has a "weather" block."""
    tool_config = config.get("tool_config") or {}
    if isinstance(tool_config, str):
        try:
            tool_config = json.loads(tool_config)
        except json.JSONDecodeError:
            tool_config = {}
    settings = tool_config.get("weather")
    if not settings:
        return []
    units = settings.get("units", "metric") if isinstance(settings, dict) else "metric"

    def get_forecast(city: str, days: int = 1, tool_context: ToolContext = None) -> Dict[str, Any]:
        """Get the weather forecast for a city.

        Args:
            city: Name of the city, for example "Belgrade".
            days: How many days ahead, 1 to 7.
        """
        if not 1 <= days <= 7:
            return {"status": "error", "error_message": "days must be between 1 and 7"}
        forecast = _fetch(city, days, units)
        return {"status": "success", "city": city, "forecast": forecast}

    return [get_forecast]
```

What the conventions are for:

- **`config` is the whole agent**, as a dict: `name`, `project_id`, `tool_config`
  and the rest. Bind what the tool needs in a closure, as `units` is here. That is
  how the memory tools know their project.
- **The signature and docstring are the tool's interface.** The model sees the
  function name, the typed parameters and the docstring. Write the docstring for
  the model: when to use the tool and what each argument means.
- **`tool_context` is injected** by the runtime and hidden from the model. Use it
  for the caller's identity and session state. Keep to common attributes if the
  tool should also work on the LangGraph runtime.
- **Return a dict with a `status`.** For an expected failure, return
  `{"status": "error", "error_message": ...}` so the model can react. Let unexpected
  exceptions propagate: a broad `try/except` hides the failure from the framework's
  retry.
- **Plain `def` is fine for slow work.** The factory wraps synchronous tools so they
  run in a worker thread and do not block the event loop.

## 2. Register it

In `shared/utils/tools/tool_factory.py`, add the key to `_tool_creators` in
`ToolFactory.__init__` and a creator method that imports the module lazily:

```python
            'weather': self._create_weather_tools,
```

```python
    def _create_weather_tools(self, config: Dict[str, Any]) -> List[Any]:
        """Create weather forecast tools."""
        from .weather_tools import create_weather_tools_from_config
        return create_weather_tools_from_config(config)
```

The creator runs when `tool_config` contains the key with any non-null value, and
receives the agent config. The first line of the creator's docstring becomes the
tool's description in the generated [tool reference](../reference/tools.md).

Code outside the repository can register a creator at runtime instead:
`get_tool_factory().register_tool_creator("weather", fn)`.

After the creators run, `create_tools` applies three wrappers to every tool, in this
order: tracing, the thread offload, and the approval wrapper for names listed under
`require_confirmation`. Every agent also receives `update_user_profile` and
`get_user_profile`, whatever its configuration.

## 3. The dashboard switch

The JSON key works as soon as the tool is registered. To give it a checkbox in the
**Tool Configuration** dialog, add it to
`templates/dashboard/modals/config_modals.html` and to the code that reads and
writes the checkboxes in `static/js/agent-tools.js`. Add the key to the
"Available keys" hint in the same template either way.

## 4. Decide who may reach it

A tool runs with the server's permissions, for whoever is talking to the agent. Ask
what an anonymous widget visitor could do with it.

- For something dangerous, follow `_create_code_executor_tools`: it refuses to load
  on an agent that has a widget key.
- For a write that only some people should trigger, check the caller's roles inside
  the tool, as `memory_blocks_tools.py` does for create, modify and delete.
- Read secrets from the environment, never from `tool_config`: the config is stored
  in the database and in the agent's version history.
- Build filesystem paths from user input only through
  `shared/utils/path_safety.resolve_within_base`.

## 5. Test and document

- Add `shared/test/test_weather_tools.py`. Call the factory with a config dict and
  call the returned functions directly; no agent server is needed. Run
  `python -m unittest shared.test.test_weather_tools -v`.
- Run `python scripts/gen_docs.py` and commit the updated tool reference.
- Add a row for the tool to the table in `docs/user/tools-and-mcp.md`.

## Custom functions

The `custom_functions` key is a lighter path for one-off functions. Its value is a
list of names; for each, `load_custom_function` imports a module of that name from
`custom_tools.<name>` or `tools.<name>` and takes the function with the same name,
or one called `main` or `tool`. A name that cannot be found is logged and skipped.
