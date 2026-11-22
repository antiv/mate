---
title: Help in the dashboard
summary: Ask the built-in help agent how to do something; it answers from this documentation.
audience: user
order: 12
covers:
  - static/js/help-panel.js
  - static/css/help-panel.css
  - shared/utils/tools/docs_tools.py
  - shared/sql/migrations/sqlite/V036__mate_help_agent.sql
  - shared/sql/migrations/postgresql/V036__mate_help_agent.sql
  - shared/sql/migrations/mysql/V036__mate_help_agent.sql
---

# Help in the dashboard

Every dashboard page has a **?** button in the bottom-right corner. It opens
**MATE Help**, a chat with a built-in agent that answers questions about using MATE
from this documentation.

## Ask a question

1. Click **?**.
2. Type your question and press **Enter** (**Shift+Enter** starts a new line).

The question goes with the name of the page you are on, so "what does this switch
do?" on the **Triggers** page is understood as a question about triggers. The agent
searches the documentation, answers in the language you write in, and names the page
the answer comes from. When the documentation does not cover something, it says so
rather than guessing.

The conversation stays when you move to another page. **New chat** starts over.

## What it can see

| You are | The agent searches | Links in answers |
|---|---|---|
| An admin | The user guides, the developer guides and the reference | Open the page in **Documentation** |
| Anyone else signed in (role `user`) | The user guides only | None, since only admins can open **Documentation** |

People with the `pending` role are refused, as with any agent open to `admin` and
`user`. The agent only reads documentation; it cannot change anything in MATE.

## The help agent

The agent is called `mate_help` and sits in its own project, **MATE Help**. It is
created by a database migration, so every installation has it. Open it on the
**Agents** page like any other agent.

- **Choose the model** in the agent's **Model** field. It starts with none set, which
  means the server's default model (`GEMINI_MODEL`, or `gemini-2.5-flash`). A fallback
  model and a custom endpoint and key can be set the same way as for any agent; see
  [Agents](agents.md).
- **Change who may use it** under **Allowed roles**.
- **Change how it answers** in its instruction.

Your changes are kept: the migration creates the agent only when no agent named
`mate_help` exists, and never edits it afterwards.

The **?** button appears only while the agent server lists `mate_help`. Disable or
delete the agent and the button goes away.

## Give the documentation to another agent

The help agent's documentation search is an ordinary tool. To give it to any agent,
add `"docs": true` to the agent's **Tool Configuration** JSON. The agent gets two
functions: `search_docs` and `read_doc_page`. It sees the same sections as the person
it is talking to, as in the table above.
