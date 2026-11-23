---
title: Work Room
summary: Chat with agents, return to earlier conversations, and use the coding and browser panels.
audience: user
order: 50
covers:
  - templates/dashboard/workroom.html
  - static/js/widget/chat.js
  - shared/utils/dashboard/dashboard_server.py::*workroom*
---

# Work Room

The Work Room is where you talk to agents. It is the one space everyone who can sign
in has, whatever their role.

## Start a conversation

Select an agent, type a message, and press Enter or the arrow. Shift+Enter starts a new
line. The list holds the agents your
roles let you use. **Search agents** above it keeps the agents whose name or
description holds every word you type; Enter opens the agent when only one is left,
and Escape clears the search. If no agent is offered, none you may use has been created yet; an
administrator creates them on the **Agents** page, and sets who may use each.

If an agent answers with an access-denied message, it names the roles it requires
and the roles you have. Pass that on to your administrator.

The **+** beside the message attaches images, for agents whose model can read them, PDFs
and text files. While the agent answers, the arrow is a square that stops it.

## Conversations

Each conversation is kept. The sidebar shows them under **Recent Sessions**, newest
first. Each is named after the first reply, by a model when the administrator has chosen
one (`TITLE_GEN_MODEL`) and otherwise by the first words of your message. An older one
without a name gets it with the next reply. It shows
20; **Show more** adds 20 more.

**Search sessions** above the list keeps the conversations whose name holds every
word you type. Press Enter to search what was said as well: the conversations whose
messages hold the words are added, each with the passage that matched. It searches
only your own conversations with this agent, the latest 5,000 messages of them.
Escape clears the search. **New Conversation**
starts a fresh one with the same agent; the agent does not carry anything over from
the old conversation unless it stores it in a memory block or in your profile.

## Rate an answer

Each reply has a thumbs-up and a thumbs-down. After a thumbs-down you can add a short
note about what went wrong. Ratings are not sent to the agent; they go to your
administrator, who can turn a rated-down answer into a test case so the same mistake
is caught next time. See [Evals](evals.md).

## The coding panel

When an agent writes code, open the **Coding panel** to see it next to the chat.
There you can:

- **Copy** or **Download** the code
- open it in a **New Tab**
- **Run** it, for languages that can run in the browser. Python runs inside your
  browser, not on the server; Dart and Flutter code opens in DartPad. HTML,
  JavaScript and Python run in a sandbox, apart from the dashboard: code an agent
  wrote cannot act as you in MATE, read your session, or change the page.

Running code in the panel is separate from the agent's own Code Executor tool, which
runs on the server.

## The browser panel

Agents with the Browser tool drive a real browser on the server. Open the
**Browser panel** to watch it. You can also take over: click in the view, type, and
scroll, for example to sign in to a site before handing back to the agent.

- **Clear Site** removes cookies and stored data for the current website.
- **Reset Profile** deletes all browser data and signs the browser out of every
  site.

The browser belongs to your account; other people do not see or share it.

## Images and files from an agent

Images an agent generates appear in the conversation. Files it produces are offered
as links.
