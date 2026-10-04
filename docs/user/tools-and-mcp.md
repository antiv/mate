---
title: Tools and MCP servers
summary: Give an agent built-in tools, connect external MCP servers, and require approval before a tool runs.
audience: user
order: 30
covers:
  - templates/dashboard/modals/config_modals.html
  - static/js/agent-tools.js
  - shared/utils/tools/tool_factory.py
  - shared/utils/tools/mcp_tools.py
---

# Tools and MCP servers

A tool is something an agent can do besides writing text: search the web, read a
file, call another system. Tools are set per agent, in the agent form on
**Studio → Agents**.

There are two kinds. **Built-in tools** ship with MATE and are switched on in
**Tool Configuration**. **MCP servers** are external tool providers that you connect
in **MCP Servers Configuration**.

## Switch on built-in tools

Open the agent, click **Configure** under **Tool Configuration**, tick what the agent
needs and save. The same settings are shown as JSON on the right; some tools can
only be adjusted there.

| Tool | What the agent can do | Needs |
|---|---|---|
| Web search (`google_search`) | Search the web. | Nothing. With a Tavily key on the server, results come from Tavily; without one, from DuckDuckGo. |
| **Google Drive** | List and read files in a Drive folder. | A Google service account on the server. |
| **Google Calendar** | Check availability, list and create events. | The same service account, and the calendar shared with its email address. Set the calendar, timezone, working hours and appointment length in the dialog. |
| **Browser** | Open pages, click and type in a browser running on the server. Watch it in the Work Room's Browser panel. | Private and local addresses are blocked unless the administrator allows them. |
| **Image Tools** | Generate images. Choose the model in the dialog. | An API key for the chosen model's provider. |
| **Image Data Extraction** | Read structured data out of an image. | A vision model, set in the dialog. |
| **Memory Blocks** | Read and update the project's shared notes. See [Memory blocks](memory-blocks.md). | Nothing. |
| **Create Agent** | Create, read, change and delete other agents. | Nothing. Give this only to agents you trust to redesign your setup. |
| **Code Executor** | Run Python and shell commands on the server. | See the warning below. |
| **Shop (E-commerce)** | Show a catalog, keep a cart, take orders. Edit the catalog, currency and shop name in the JSON. | `vendor_email` to email orders; `partner_key` to list them under **Shop Orders**. |
| **Subagent Delegation** | Split a task and hand the parts to temporary helper agents that run in parallel. | Nothing. Limits and the helpers' model are set in the JSON. |
| **CV Tools** | Work with CVs stored in Google Drive. | The Google service account. |

The [tool reference](../reference/tools.md) lists every key the JSON accepts.

Every agent can also read and update the profile of the person it is talking to,
without any configuration. Profiles are shown on the **Users** page.

> **The code executor is not a sandbox.** Whatever the agent decides to run, runs on
> the server with the server's permissions. MATE refuses to load it on an agent that
> has a widget key, because anyone visiting the site could then run commands on your
> host.

## Ask before a tool runs

To make an agent pause and wait for the person's approval before it uses a tool,
list the tool's function name under `require_confirmation` in the JSON:

```json
{
  "memory_blocks": true,
  "require_confirmation": ["delete_shared_block"]
}
```

Pausing and resuming depends on the server's resumability setting
(`RESUMABILITY_ENABLED`); ask your administrator if approvals do not appear.

## Connect an MCP server

MCP (Model Context Protocol) is a standard way for a program to offer tools to
agents. Many products publish an MCP server; connecting one gives the agent all of
its tools.

Open the agent, click **Configure** under **MCP Servers Configuration** and enter a
JSON mapping with one entry per server:

```json
{
  "mcpServers": {
    "issues": {
      "url": "https://mcp.example.com/mcp",
      "headers": { "Authorization": "Bearer ${ISSUES_TOKEN}" }
    },
    "files": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/data"]
    }
  }
}
```

- An entry with a `url` is reached over HTTP. Add `headers` for authentication.
- An entry with `command` and `args` is started on the MATE server as a subprocess.
  `env` passes it environment variables.
- `timeout` is the number of seconds to wait for a tool call (60 if not set).
- Write a secret as `${NAME}` and MATE reads it from the server's environment
  instead of storing it with the agent. If that variable is not set, the server is
  skipped rather than called without its credential.

## Use your agents from other tools

It also works the other way round: an administrator can expose chosen agents as MCP
servers, at `/agents/<agent name>/mcp`, so that other MCP clients can call them.
This is switched on for named agents with the `MCP_EXPOSED_AGENTS` setting.
