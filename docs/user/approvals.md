---
title: Approvals
summary: Approve or reject the tool calls agents paused on when nobody was chatting, and look up every decision.
audience: user
order: 37
covers:
  - templates/dashboard/approvals.html
  - static/js/approvals-page.js
  - shared/utils/dashboard/dashboard_server.py::*approval*
---

# Approvals

An agent can be set to ask before it uses a tool: see
[Ask before a tool runs](tools-and-mcp.md#ask-before-a-tool-runs). In a chat, the
person chatting answers. Requests wait on the **Approvals** page instead when:

- nobody is chatting: a trigger, a call through the API, A2A or MCP, Slack, an eval;
- the agent's `approver` is `inbox`: an administrator decides, not the person
  chatting, whose card says it is waiting;
- the agent's `approver` is `either`: an administrator or the person chatting,
  whoever answers first.

The agent's `notify` setting, or your administrator's default, sends a message when
a request starts waiting. The message has a link that opens the request on this page.

**Approvals** is in the Control Room, under **Governance & Access**. It is for admins
of the workspace. The number beside it counts the requests waiting.

## Waiting

The **Waiting** tab lists the requests that wait for you, the longest-waiting first:

| Column | Shows |
|---|---|
| **Waiting** | How long the run has been stopped |
| **Agent**, **Tool** | The agent and the tool it wants to run |
| **Source** | Where the run came from: `chat`, `trigger`, `api`, `a2a`, `mcp`, `slack` or `eval` |
| **Started by** | The user the run belongs to, such as `trigger_runner` for a trigger |
| **Expires** | When the request is rejected if nobody decides |

The list refreshes every 30 seconds. **Search requests** narrows it by any of the
columns.

Click the tool's name, or **Review**, to open the request. It shows:

- the arguments the agent wants to call the tool with;
- the tool's own explanation, if it has one;
- **Open the session**, which opens the conversation the run belongs to on
  **Session Tracking**.

Then:

- **Approve** runs the tool, and the agent carries on.
- **Reject** tells the agent it may not use the tool, and the agent carries on
  without it.
- **Note** is optional. It is kept with the request and in the audit log, so say why
  when it isn't obvious.

The run continues in the background, so the dialog closes at once. A trigger sends
its answer to its destination once every request of its run is decided (see
[Triggers](triggers.md)).

If someone else decided the request first, you are told so, and the lists show what
they chose.

## History

The **History** tab lists every request of the workspace, newest first, including
those a person answered in a chat:

| Column | Shows |
|---|---|
| **Decision** | `waiting`, `waiting in the chat`, `approved`, `rejected`, `expired` (nobody decided in time) or `cancelled` (the agent was deleted) |
| **Decided by** | Who decided, and where: `inbox`, `chat`, or `system` for an expiry |
| **Run** | After a decision in the inbox or an expiry: `resuming`, `continued`, or `failed` if the run could not be continued |

Filter it by decision and by source, or search by agent, tool or user. It shows 25
requests at a time.

**View** opens a request with:

- its arguments;
- the note;
- what the agent answered once the run continued, or why it could not continue.

A request **waiting in the chat** is answered by the person chatting, not here.

A decision reaches the agent once. If the person chatting clicks the card after you
decided, or clicks it twice, the agent gets only the first answer.

## When nobody decides

A request expires after a day, and the run continues as if it had been rejected.
The agent's `expires_after_minutes` sets its own wait. Your administrator can change
the default with `APPROVALS_EXPIRE_MINUTES`, in minutes.

Deleting an agent cancels its waiting requests.

## The record

Each decision, expiry and cancellation is also in the audit log, as
`approval.approved`, `approval.rejected`, `approval.expired` or
`approval.cancelled` (see [Usage and audit logs](usage-and-audit.md)).
