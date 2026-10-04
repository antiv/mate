---
title: Agents
summary: Create and configure agents, connect them into a tree, and control who can use them.
audience: user
order: 20
covers:
  - templates/dashboard/agents.html
  - templates/dashboard/modals/agent_modal_macro.html
  - templates/dashboard/modals/config_modals.html
  - static/js/agent-forms.js
  - static/js/agent-management.js
  - shared/utils/rbac_middleware.py
  - shared/utils/utils.py::create_model
  - shared/utils/dashboard/dashboard_server.py::*agent*
---

# Agents

An agent is a model plus the instruction, tools and limits you give it. Agents are
managed in **Studio → Agents**. Choose a project first; the page then lists that
project's agents as a tree.

## Create or edit an agent

Click **Add Agent**, or the pencil in an agent's row. Saving an agent reloads it, so
the next message already uses the new configuration.

### The basics

| Field | What to enter |
|---|---|
| **Name** | A unique identifier. Other agents and triggers refer to the agent by this name. |
| **Type** | **LLM** for an ordinary agent. **Loop** repeats its sub-agents up to **Max Iterations** times. **Graph** runs its sub-agents as a workflow with routing. |
| **Project** | The project the agent belongs to. |
| **Model Name** | Which model answers. See [Choosing a model](#choosing-a-model). |
| **Description** | What the agent is for, in a sentence. A parent agent decides which sub-agent to hand work to by reading these, so a missing or vague description means the agent is not picked. |
| **Instruction** | The agent's standing orders. Click the field to write them in a Markdown editor. |
| **Allowed Roles** | Who may use the agent, as a JSON list such as `["user", "admin"]`. See [Who can use an agent](#who-can-use-an-agent). |

### Choosing a model

The prefix of the model name selects the provider:

| Model name | Provider |
|---|---|
| empty | the installation's default (`gemini-2.5-flash` unless your administrator changed it) |
| `gemini-2.5-flash` | Google Gemini |
| `openai/gpt-4o` | OpenAI |
| `anthropic/claude-...` | Anthropic |
| `openrouter/provider/model` | OpenRouter |
| `ollama_chat/llama3.2` | a local Ollama server |
| any other `provider/model` | that provider, through LiteLLM |

Each provider needs its API key set on the server; the
[configuration reference](../reference/configuration.md) lists them.

Three optional fields change where the model comes from:

- **Model Base URL** points the agent at an OpenAI-compatible endpoint you already
  run. The model name then needs a provider prefix, for example `openai/my-agent`.
- **Model API Key** is the key for that endpoint. Write `${MY_KEY}` to read it from
  the server environment. A key typed literally is stored in the database and in
  the agent's version history.
- **Fallback Model** answers when the main model fails, so a provider outage does
  not reach the person chatting. It uses the provider's key from the server, not
  the base URL or key above.

## Connect agents into a tree

**Parent Agents** lists the agents this one works under, as a JSON list of names.
Click the field to pick them. An agent with no parents is a root; an agent can have
more than one parent.

A parent hands a request to the sub-agent whose description fits it best. Build a
tree by giving the root a broad instruction ("route each request to the right
specialist") and each sub-agent a narrow one with a precise description.

**Visual Builder** shows the same tree as a diagram and lets you edit it there.

## Give an agent abilities

Each of these opens its own dialog from the agent form:

| Section | What it does |
|---|---|
| **Memory Blocks** | Named notes the agent can read and update, shared within the project. |
| **Tool Configuration** | Switch on built-in tools, with checkboxes or as JSON: web search, Google Drive and Calendar, browser, image generation, code executor, shop, creating other agents, delegating to runtime sub-agents. The [tool reference](../reference/tools.md) lists every key. |
| **File Search (RAG)** | Upload documents the agent can search when answering. |
| **MCP Servers Configuration** | Connect external MCP servers as a JSON mapping. A server with a `url` is reached over HTTP; one with `command` and `args` runs on the server as a subprocess. Write secrets as `${AUTH_TOKEN}` to read them from the server environment. |
| **Planner Config** | Let the model plan before acting, and set its thinking mode. For Graph agents this is also where the workflow's edges are defined. |
| **Guardrails** | Checks on what goes in and what comes out: personal data, prompt injection, content policy. |
| **Generate Content Config** | Temperature, token limits and sampling. |
| **Input Schema**, **Output Schema** | JSON schemas that validate what the agent receives and shape what it returns. |
| **Include Contents** | Whether the agent sees the earlier conversation (**Default**) or only the current request (**None**). |

The code executor runs commands on the server and is not a sandbox. MATE refuses to
load it on an agent that has a widget key, because anonymous visitors could then run
commands on the host.

## Who can use an agent

**Allowed Roles** is compared with the roles on the user record of the person
chatting. The rule has no exceptions, not even for administrators:

- With a list, the person needs at least one of the listed roles. `["user"]` admits
  everyone with the `user` role, and nobody who has only `admin`. Use
  `["user", "admin"]` to admit both.
- **Left empty, the agent admits only people with the `admin` role.**

A person who is refused gets a message naming the roles required and the roles they
have, and the refusal is recorded in the audit log. Roles are assigned on the
**Users** page; see [Users and roles](users-and-roles.md).

> **The built-in account does not start with the `admin` role.** Its user record is
> created the first time it talks to an agent, with the role `user`. Until you add
> `admin` to it on the Users page, an agent with an empty list refuses it too.

## Switches at the bottom of the form

| Switch | Effect |
|---|---|
| **Disabled** | Takes the agent out of service without deleting it. |
| **Hardcoded** | Marks an agent whose code lives in the repository rather than in the database. |
| **Expose as Model** | Makes the agent callable as a model through the OpenAI-compatible API. |
| **Debug Mode** | Shows detailed error messages in the chat instead of a generic one. |

## AI disclosure

People who chat with an agent through the embedded widget are told they are talking
to an AI. **AI Disclosure** replaces the default wording, for example to translate
it. Filling in **Reason for not disclosing** hides the notice and records why; the
EU AI Act requires the notice unless it is already obvious to the person.

## History, copies and moving agents

- **History**, in the agent form, lists earlier versions of the agent so you can
  compare and restore them.
- **Clone to Project** copies an agent and its whole sub-agent tree into another
  project, optionally with the project's memory blocks and file-search stores. The
  source is not changed.
- **Export** and **Import** move a project's agents between installations as a file.
- **Save as Template** turns a root agent and its tree into a reusable template.
- **Download Binary** builds a standalone executable of an agent.
- **Reload All** makes every agent pick up its configuration again, for the rare
  case where a change did not take effect.
