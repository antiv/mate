---
title: Agents
summary: Create and configure agents, connect them into a tree, and control who can use them.
audience: user
order: 20
covers:
  - templates/dashboard/agents.html
  - templates/dashboard/agent_page.html
  - templates/dashboard/modals/config_modals.html
  - static/js/agent-page.js
  - static/js/agent-forms.js
  - static/js/agent-utils.js
  - static/js/agent-management.js
  - shared/utils/rbac_middleware.py
  - shared/utils/utils.py::create_model
  - shared/utils/dashboard/dashboard_server.py::*agent*
  - shared/utils/agent_cleanup.py
---

# Agents

An agent is a model plus the instruction, tools and limits you give it. Agents are
managed in **Studio → Agents**. Choose a project first; the page then lists that
project's agents as a tree.

## The agent's page

Each agent has a page of its own, `/dashboard/agents/<name>`. Open it by clicking the
agent's row on the Agents page, or **Advanced Setup** in the Visual Builder; its link
at the top then leads back to the canvas. **Add Agent** opens an empty one,
`/dashboard/agents/new`. An agent of another workspace is not found.

The top of the page names the agent, its type, model and project, and holds:

- **Test**, for a root agent: a chat with it in the Work Room;
- **⋯**: **Copy**, **Open in Visual Builder**, **Export** (a root agent and its tree,
  as a file) and **Delete**.

Under it are the tabs. The page's address names the tab open (`#versions`), so a
reload or a link comes back to it.

- **Settings**: the agent's editor, below.
- **Versions**: earlier versions of the agent, see
  [below](#history-copies-and-moving-agents).
- **Sessions**: the conversations with the agent, from every surface, newest first,
  with a search box. **Open** shows one in the Session Inspector of
  [Session Tracking](sessions-and-traces.md). A sub-agent has no conversations of
  its own: the tab lists those of the root agent it works under, and with more than
  one root, a list chooses which.

A new agent has only **Settings** until it is created.

## Edit an agent

The editor has three columns. On a narrow screen they stack in this order.

- **Left, the basics:** name, type, project, model, description, parent agents,
  allowed roles and three switches.
- **Centre, the instruction,** in a Markdown editor.
- **Right, everything else,** one section each. A section is closed until you click
  it, and says on its line whether it is set: `3 on` for three tools, `—` for
  nothing.

When you change something, a bar appears at the bottom: **Save** stores the change,
**Discard** puts back what was saved. Leaving the page with changes not saved asks
first. Each save makes a version of the agent and reloads it, so the next message
already uses the new configuration.

**Add Agent** shows the same editor with **Create agent**. **Copy** fills it from the
agent copied, with `_copy` after the name and an empty **API key**, since the page
never shows a stored key. Creating a root agent asks whether to make a folder for it
under the server's `agents/` folder.

### The basics

| Field | What to enter |
|---|---|
| **Name** | A unique identifier on the whole installation. Other agents and triggers refer to the agent by this name. It starts with a letter or an underscore and holds only letters, digits and underscores, for example `support_root`; another name is refused. A name already in use, in any workspace, is refused with "This name is taken". |
| **Type** | **LLM** for an ordinary agent. **Loop** repeats its sub-agents up to **Max iterations** times, under **Advanced**. **Graph** runs its sub-agents as a workflow with routing. |
| **Project** | The project the agent belongs to. |
| **Model** | Which model answers. See [Choosing a model](#choosing-a-model). |
| **Description** | What the agent is for, in a sentence. A parent agent decides which sub-agent to hand work to by reading these, so a missing or vague description means the agent is not picked. |
| **Parent agents** | The agents this one works under. See [Connect agents into a tree](#connect-agents-into-a-tree). |
| **Allowed roles** | Who may use the agent. See [Who can use an agent](#who-can-use-an-agent). |
| **Instruction** | The agent's standing orders, in the centre column. |

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
[configuration reference](../reference/configuration.md) lists them. A workspace
that may not use the server's keys calls a model only with a key of its own, and
otherwise fails with "This workspace has no key for" and the provider; see
[Workspaces](workspaces.md).

Three optional fields change where the model comes from. The first two are in the
**Model endpoint** section, the third under **Model**:

- **Base URL** points the agent at an OpenAI-compatible endpoint you already
  run. The model name then needs a provider prefix, for example `openai/my-agent`.
- **API key** is the key for that endpoint. Write `${MY_KEY}` to read it from
  the server environment. A key typed literally is stored in the database and in
  the agent's version history, encrypted when the server has `MATE_SECRET_KEY`.
- **Fallback Model** answers when the main model fails, so a provider outage does
  not reach the person chatting. It uses the provider's key from the server, not
  the base URL or key above.

## Connect agents into a tree

**Parent agents** lists the agents this one works under. Pick one from the list
below it to add it; the × on a name removes it. The list offers the workspace's other
agents. An agent with no parents is a root; an agent can have more than one parent.

A parent hands a request to the sub-agent whose description fits it best. Build a
tree by giving the root a broad instruction ("route each request to the right
specialist") and each sub-agent a narrow one with a precise description.

**Visual Builder** shows the same tree as a diagram and lets you edit it there.

## Give an agent abilities

The sections of the right-hand column. Click one to open it and set it there: the
checkboxes and fields, and the JSON beside them, follow each other as you type, and
**JSON** swaps the box for a code editor. **Clear** empties a section. A change is
stored with the next **Save**.

| Section | What it does |
|---|---|
| **Tools** | Switch on built-in tools, with checkboxes or as JSON: web search, Google Drive and Calendar, browser, image generation, code executor, shop, creating other agents, delegating to runtime sub-agents. The [tool reference](../reference/tools.md) lists every key. **Approval settings** sets which tools wait for a person's approval; see [Approvals](approvals.md). |
| **MCP servers** | Connect external MCP servers as a JSON mapping. A server with a `url` is reached over HTTP; one with `command` and `args` runs on the server as a subprocess. Write secrets as `${AUTH_TOKEN}` to read them from the server environment. |
| **File Search** | Upload documents the agent can search when answering. Once the agent is saved. |
| **Memory blocks** | Named notes the agent can read and update, shared within the project. **Manage Blocks** shows once the Memory Blocks tool is on. |
| **Guardrails** | Checks on what goes in and what comes out: personal data, prompt injection, content policy. |
| **Planner** | Let the model plan before acting, and set its thinking mode. For Graph agents this is also where the workflow's edges are defined. |
| **Generation** | Temperature, token limits and sampling. |
| **Input/output** | JSON schemas that validate what the agent receives and shape what it returns, and **Include contents**: whether the agent sees the earlier conversation (**Default**) or only the current request (**None**). |
| **Model endpoint** | **Base URL** and **API key**; see [Choosing a model](#choosing-a-model). |
| **AI disclosure** | See [AI disclosure](#ai-disclosure). |
| **Advanced** | **Max iterations** of a Loop agent, and **Hardcoded**. |

The code executor runs commands on the server and is not a sandbox. MATE refuses to
load it on an agent that has a widget key, because anonymous visitors could then run
commands on the host.

### What only a platform administrator can set

Some settings run code on the MATE server, or read its configuration, so they belong
to the people who run the whole installation, the platform administrators (the
built-in account, and anyone with the `admin` role):

- the **code executor** tool;
- an MCP server with `command` and `args`, started on the server;
- `${NAME}` in **API key**, **Base URL** or the MCP servers configuration;
- **Hardcoded**, and a name that already has a folder in the server's `agents/`
  folder;
- the **Google Drive**, **CV Tools**, **Google Calendar** and **Supabase Storage**
  tools, which use the server's own accounts, shared by every workspace, and the
  **Shop** tool's `vendor_email` and `partner_key` (the server's mail account, the
  platform's **Shop Orders**).

An owner or admin of a workspace who is not a platform administrator can't add these
to an agent, or change them on one: saving is refused with "Only a platform
administrator can give an agent ...". What a platform administrator set up keeps
working, and the rest of the agent can still be edited. While doing so, leave these
fields as they are, and **Base URL** too when the key is a `${NAME}`.

To give an agent with a stored **API key** a new **Base URL**, type the
key again: otherwise the stored key would be sent to the new address, and saving is
refused.

In a workspace other than Default, the code executor and MCP servers with a command
run only if a platform administrator has enabled code for that workspace, and those
four tools only if they have enabled the server's credentials. Otherwise the agent
answers without them. Outside the Default workspace, a
**Base URL** or MCP server URL on the server's own network (such as
`localhost` or `10.x.x.x`) is refused too, and a model endpoint that redirects
elsewhere fails the answer instead of being followed.

The same holds for a copy. A clone's **Name suffix** may hold only letters, digits
and underscores. A clone, template import or template update can't create an agent
whose name has a folder in `agents/`: a clone adds `_2` to the name, and a template
import asks for another project name. A clone that would change one of the settings
above, for example an MCP server whose command names the agent, is refused.

## Who can use an agent

**Allowed roles** is compared with the agent roles the person chatting holds in the
workspace. The workspace's owners and admins, and platform administrators, pass it
whatever the list:

- With roles, the person needs at least one of them. `user` admits every member who
  has the `user` role.
- **With none, the agent admits only the workspace's owners and admins and platform
  administrators.** The editor says so under the field.

Type a role and press Enter, or pick one the workspace already uses: the list offers
the roles of the workspace's members and agents, and each role on the agent says how
many members hold it, so a role nobody has stands out. A role is letters, digits,
`_`, `.` and `-`. Add `widget` for an agent that answers on a website: the widget's
anonymous visitors get that role ([Widget](widget.md)).

A person who is refused gets a message naming the roles required and the roles they
have, and the refusal is recorded in the audit log. Members' roles are set on the
**Members** page; see [Users and roles](users-and-roles.md).

## Switches

| Switch | Where | Effect |
|---|---|---|
| **Disabled** | Basics | Takes the agent out of service without deleting it. |
| **Expose as model** | Basics | Makes the agent callable as a model through the OpenAI-compatible API. |
| **Debug mode** | Basics | Shows detailed error messages in the chat instead of a generic one. |
| **Hardcoded** | Advanced | Marks an agent whose code lives in the repository rather than in the database. Only a platform administrator can set it; for others it is greyed out. |

## AI disclosure

People who chat with an agent through the embedded widget are told they are talking
to an AI. In the **AI disclosure** section, **Notice** replaces the default wording, for example to translate
it. Filling in **Reason for not disclosing** hides the notice and records why; the
EU AI Act requires the notice unless it is already obvious to the person.

## History, copies and moving agents

- **Versions**, a tab of the agent's page, lists earlier versions of the agent. Click
  one to see what it changed from the version before; **Rollback** restores it, and
  the page reloads on it; **Run Evals** runs the agent's test cases on it; the tag
  icon names a version. Restoring is refused when the version would bring back
  a name another agent has since taken, a parent agent outside the workspace, or a
  project of another workspace.
- **Clone to Project** copies an agent and its whole sub-agent tree into another
  project, optionally with the project's memory blocks and file-search stores. The
  source is not changed.
- **Export** and **Import** move a project's agents between installations as a file.
  An import only writes to projects of the current workspace: an agent, memory block
  or trigger naming another project is skipped and listed in the result, and so is
  an agent whose name another workspace uses, even with overwriting on. An agent or
  memory block that names no project goes to project 1, and to the workspace's
  oldest project when the workspace doesn't have project 1. An imported
  trigger goes to the project of its agent, and is skipped when that agent is not in
  the workspace.
- **Save as Template** turns a root agent and its tree into a reusable template.
- **Download Binary** builds a standalone executable of an agent.
- **Reload All** makes every agent pick up its configuration again, for the rare
  case where a change did not take effect.

## Delete an agent

Deleting an agent, or the project it is in, cannot be undone. It removes, with the
agent:

- what it is connected to: its file-search store links, widget keys, Slack
  integrations and triggers;
- its settings: budgets, alert rules, eval test cases and their results, version
  history;
- what people left with it: their conversations with it, the memory built from
  them, and the accounts they connected for it (Google Drive, Calendar).

Usage, guardrail, feedback and audit logs stay, so the cost and history of what it
did remain on the Usage and Audit Log pages. So does the record of its approval
requests; any still waiting for a decision are cancelled.

This is so that an agent created later with the same name starts with none of it.
Before MATE 1.6 these were left behind and passed to the new agent.

Renaming an agent takes all of this along to the new name, conversations included:
its widget keys, Slack integrations and triggers keep working, and the old name is
free with nothing left under it. Its sub-agents name the new name as their parent,
so the tree stays whole. Its ratings go along too, those given in the workspace's
projects; none is deleted.

A rename before 2.0 left them under the old name. In a workspace other than Default
such a name is refused as taken. In the Default workspace an agent may take it, and
takes over what was left, as before 2.0; where both names hold the same conversation,
or stored state for the same person, the renamed agent's is kept.
