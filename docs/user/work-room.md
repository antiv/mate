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

Select an agent, type a message and press **Send**. If no agent is offered, none has
been created yet; an administrator creates them on the **Agents** page.

If an agent answers with an access-denied message, it names the roles it requires
and the roles you have. Pass that on to your administrator.

The paperclip attaches an image to your message, for agents whose model can read
images.

## Conversations

Each conversation is kept. The sidebar shows them under **Recent Sessions**, named
automatically after the first reply so you can find them again. **New Conversation**
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
  browser, not on the server; Dart and Flutter code opens in DartPad.

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
