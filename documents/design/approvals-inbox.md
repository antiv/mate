# Design: approvals inbox

Status: proposal for 1.5.0, to be built later. Tracking: #178, feature 3.

## Problem

An agent can be told to ask before it runs a tool:
`tool_config: {"require_confirmation": ["delete_shared_block"]}`. The run then
stops and emits an `adk_request_confirmation` function call. Someone has to answer
it with a function response `{confirmed: true|false}` on the same session.

Today only a chat can answer. The widget, the Work Room and the standalone chat
(`static/js/widget/chat.js`, `static/js/standalone/chat.js`) draw an approve/reject
card, and the person who is chatting decides.

That leaves two gaps.

1. **Runs with nobody in a chat.** These are trigger firings (cron and webhook),
   A2A calls, MCP calls and the OpenAI-compatible API. The run stops at the
   confirmation, and nothing can answer it.
   - `TriggerRunner._invoke_agent` keeps only the last text of the stream, so a
     firing that stopped for approval is recorded as `ok` with an empty or partial
     reply. The tool call is quietly dropped.
   - So `require_confirmation` and triggers can't be used together, which rules out
     the cases where approval matters most: an agent acting on its own on a schedule.
2. **The person chatting isn't always the right one to approve.**
   - A widget visitor shouldn't approve a refund.
   - For EU AI Act Art. 14 (human oversight), the deployer names who oversees the
     system, and that person needs to see what is waiting and decide with a record.

## Goals

- A dashboard queue of tool calls waiting for approval, from any source.
- Approve or reject there, with an optional note. The paused run then continues.
- A notification when something starts waiting, by email, Slack, Discord, Teams or
  a webhook.
- An audit log entry for each decision and each expiry.
- The same behaviour on both runtimes (ADK and LangGraph).

## Non-goals (first version)

- Approve/reject buttons inside Slack (interactive messages). The first version
  sends a link to the dashboard.
- Editing the tool's arguments before approving. The choice is approve or reject.
- Multi-step approval chains (two people, or a manager after a team lead).
- Pausing MCP and OpenAI-API calls across HTTP requests. Those protocols have no
  "come back later". See [Sources](#sources).

## How it works today

| Piece | Where |
|---|---|
| Wrapping the named tools (ADK) | `ToolFactory._apply_confirmation_wrapping` → `FunctionTool(require_confirmation=True)` |
| Wrapping the named tools (LangGraph) | `shared/utils/langgraph/hitl.py`: a LangGraph `interrupt`, translated by `event_translator.py` into the same `adk_request_confirmation` event |
| Sub-agents inherit the setting | `subagent_delegation_tool.py` |
| Answering | the chat posts a `function_response` named `adk_request_confirmation` with `{confirmed}` to `/run_sse` on the same session |
| Resuming (ADK) | `google/adk/flows/llm_flows/request_confirmation.py` finds the answer in the session's events on the next run and runs or skips the tool |
| Sessions | persistent by default (`--session-db-url sqlite:///session_db.db`), so a paused session outlives a restart |

The two runtimes emit the same event and accept the same answer. So the inbox can
work at the wire level and doesn't need to know which runtime the agent uses.

## Proposal

### 1. Record each request

Add a new table, `approval_requests`:

| Column | |
|---|---|
| `id` | primary key |
| `status` | `pending`, `approved`, `rejected`, `expired`, `cancelled` |
| `agent_name`, `project_id` | the agent that asked, and its project |
| `app_name`, `user_id`, `session_id` | where the paused run lives; needed to resume it |
| `confirmation_call_id` | id of the `adk_request_confirmation` call; the answer must carry it |
| `tool_name`, `tool_args` | what will run; args as JSON, capped in size |
| `hint` | the text the tool or runtime gave |
| `source` | `chat`, `trigger`, `a2a`, `mcp`, `api` |
| `trigger_id` | when the source is a trigger, so its output can be routed after approval |
| `requested_at`, `expires_at` | |
| `decided_by`, `decided_at`, `note` | the decision |
| `resume_status`, `resume_result` | what happened when the run was resumed (`ok`, an error, the reply text, truncated) |

Migrations go in all three dialects, as usual.

**Where to capture it.** The row is written by the runtime when it emits the event,
not by the auth server. The auth server never sees A2A and MCP traffic. The
`MetricsPlugin` is registered on the ADK runtime for the same reason.

- **ADK:** a new plugin implements `on_event_callback`. It writes a row for each
  `adk_request_confirmation` function call in an event, and marks the row decided
  when it sees a matching function response. That second part covers "answered in
  the chat".
- **LangGraph:** the same two hooks go in `event_translator.py`, where the event is
  produced.

Both paths call one function, `approval_service.record_request(...)`, so the row
looks the same whichever runtime wrote it.

**Source.** The runtime can't tell a trigger from a chat by itself, so callers
mark their runs.
- **Today:** the trigger runner already runs as `user_id="trigger_runner"`.
- **New:** the trigger runner also puts the trigger id in session state when it
  creates the session.
- **To check:** whether A2A and MCP runs can be recognised by their user id, or
  need the same marker.

Anything else counts as `chat`.

### 2. Who should answer

Extend `require_confirmation`, keeping the list form as it is today:

```json
{
  "require_confirmation": {
    "tools": ["issue_refund"],
    "approver": "inbox",
    "notify": {"type": "slack", "url": "https://hooks.slack.com/..."},
    "expires_after_minutes": 1440
  }
}
```

| `approver` | Effect |
|---|---|
| `chat` (default, and the meaning of the plain list) | the person chatting decides, as today; a row is still written, so the inbox shows the history |
| `inbox` | the chat shows "waiting for approval" with no buttons; only the inbox can decide |
| `either` | both can; the first decision wins |

Runs with no chat (`source` other than `chat`) always go to the inbox, whatever
`approver` says.

### 3. Resuming the run

On approve or reject, the auth server does three things:

1. Changes the row from `pending` to the decision in one conditional update:
   `UPDATE ... WHERE id = ? AND status = 'pending'`. If no row changes, someone else
   decided first, and the reply is 409.
2. Writes the audit entry (see [Audit](#4-audit)).
3. Posts the answer to the agent server on the stored `app_name/user_id/session_id`,
   the same way `TriggerRunner._invoke_agent` posts a prompt:
   `new_message.parts = [{function_response: {id: confirmation_call_id, name: "adk_request_confirmation", response: {confirmed}}}]`.
   It reads the stream to the end and stores the outcome in
   `resume_status`/`resume_result`.

What happens next depends on the source:

- **trigger:** the reply goes through the trigger's output config (memory block,
  webhook, email), as if the firing had finished. The trigger's `last_result` gets
  `awaiting_approval` when it stops, and the final result once it has been resumed.
- **chat:** the reply lands in the session. The chat shows it the next time the
  session loads; showing it live is a later step.
- **a2a:** the reply lands in the session (see [Sources](#sources)).

The resume runs in the background, so a slow agent doesn't hold up the click. The
inbox shows the row as "resuming" until it finishes.

### 4. Audit

There is one `audit_service.log` call per decision:

| action | actor |
|---|---|
| `approval.approved` | the user who decided |
| `approval.rejected` | the user who decided |
| `approval.expired` | `system` |
| `approval.cancelled` | the user, or `system` when the agent or session is deleted |

- **Resource:** `approval_request` and the row id.
- **Details:** agent, tool, source, a hash of the arguments (not the arguments
  themselves; they stay in the row), the note, and whether resuming succeeded.

Together with the row, this records who oversaw what, when, and with what result.
That is the evidence Art. 14 asks for. It goes alongside the Art. 12 logging the
audit log already provides.

### 5. Dashboard

There is a new page, **Approvals**, under Governance & Access.

- **Tabs:** Waiting (default) and History.
- **Each row:**
  - agent, tool, source, who started the run, and how long it has waited;
  - the arguments as formatted JSON;
  - the hint;
  - Approve and Reject buttons, with a note field;
  - a link to the session.
- **Badge:** a count of waiting requests next to the menu item.
- **Who can decide:** admins, and users with a new `approver` role on the agent's
  project. The page reuses the existing role checks. Others don't see the page.
- **Sidebar and CSP:** the page uses the `data-*` actions, like every other page.

### 6. Notifications

When a row is written with `approver` ≠ `chat`, or from a run with no chat,
`notify.py` sends a message to the agent's `notify` destination:
`post_json` for webhooks, Slack, Discord and Teams, and `send_email` for email. The
formatting is the same as in `AlertService._deliver`, so that should be moved out
and shared, not copied.

- **Message:** agent, tool, a short form of the arguments, and a link to
  `/dashboard/approvals?id=…`.
- **Agents without a `notify`:** a system setting, `APPROVALS_NOTIFY_*`, gives the
  default destination.
- **Delivery:** it happens off the request path and never fails the run.

### 7. Expiry

`TriggerRunner` already runs periodic jobs (alert evaluation, cleanups). A new job
runs every minute:
- it expires pending rows past `expires_at`, with the default taken from the agent
  config or 24 hours;
- it writes `approval.expired`;
- it resumes the run with `{confirmed: false}`, so the agent is told the call was
  rejected and the session doesn't stay open forever.

## Sources

| Source | First version |
|---|---|
| Chat (widget, Work Room, standalone) | as today, plus a history row; `approver: inbox` hides the buttons |
| Trigger | stops, waits in the inbox, resumes and routes the output |
| A2A | the call returns what the run produced up to the pause. A2A has an `input-required` task state; mapping a pending approval to it, and resuming the task on decision, is the second step |
| MCP, OpenAI-compatible API | the call returns with a message that the action waits for approval, with the request id; the decision is recorded and the run resumed, but the caller is not called back |

## Security

- **Arguments may contain personal data or secrets.**
  - Only approvers and admins can read them.
  - Notifications carry a short form, never full values.
  - They are capped at 16 KB in the row.
- **Only the server resumes runs.**
  - The inbox endpoints take a request id, never a session id or call id from the
    browser.
  - A chat answer still goes through the chat user's own session, as today.
- **Deciding twice:** the conditional update makes it safe. The second click gets
  409 and doesn't send a second answer.
- **Deleted agent or session:** the row becomes `cancelled`. The inbox doesn't
  resume a run that has nowhere to go.

## Open questions

1. **Is `RESUMABILITY_ENABLED` really needed for approvals?**
   - `docs/user/tools-and-mcp.md` says approvals depend on it.
   - In ADK 2.9 the confirmation is resumed by the request processor, which reads
     the answer from the session's events. That doesn't look like it needs
     `ResumabilityConfig`.
   - Check on a running server. If the guide is wrong, fix it before building this.
2. **Several confirmations in one turn** (parallel tool calls). ADK emits one
   `adk_request_confirmation` per call. Check whether the run resumes on the first
   answer or waits for all of them, and whether each inbox decision should resume
   on its own or the inbox should collect them.
3. **The `approver` role.** Should it be per project, or is admin-only enough for
   the first version?
4. **Showing a chat the inbox's decision live.** Polling the session, or a small
   event stream? This can wait for the first version.

## Phases

1. **Table, capture and history.** The table, the plugin and translator hooks, the
   history list, and audit entries for decisions made in the chat. No behaviour
   changes.
2. **The inbox.** The Waiting tab, approve and reject, resuming, triggers
   end-to-end, and expiry.
3. **Notifications**, and the `approver`/`notify`/`expires_after_minutes` config
   with its editor in the visual builder.
4. **Later:** A2A `input-required`, Slack interactive buttons, and decisions shown
   live in the chat.

## Testing

- **Unit tests:**
  - `record_request` from both event shapes (snake_case and camelCase);
  - the conditional update under two decisions;
  - expiry;
  - the audit entries.
- **End to end, on both runtimes:**
  - a trigger whose agent has `require_confirmation`: it fires, waits in the inbox,
    is approved, the tool runs, and the output reaches its destination;
  - the same with reject and with expiry;
  - a chat with `approver: inbox`: no buttons appear, the inbox decides, and the
    reply is in the session after a reload.
