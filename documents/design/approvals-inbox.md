# Design: approvals inbox

Status: being built for 2.1.0, tracking #376 (first proposed in #178, feature 3).
Open questions 1 and 2 are settled. Phases 1, 2 and 3 are built, and reviewed (phase 4); see
[Phase 1, as built](#phase-1-as-built), [Phase 2, as built](#phase-2-as-built), [Phase 3, as built](#phase-3-as-built) and [Phase 4, as built](#phase-4-as-built-the-security-review).

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

Questions 1 and 2 were settled on 2026-11-22, on a running server (ADK 2.11.0 and
LangGraph 1.2.12), with an agent that had `require_confirmation` on
`create_shared_block`. Each case was run on ADK with `RESUMABILITY_ENABLED` on and
off, and on LangGraph. See [Settled: questions 1 and 2](#settled-questions-1-and-2).

1. **The `approver` role.** Should it be per project, or is admin-only enough for
   the first version? *Decided 2026-11-22: admins only for 2.1; a role can come later.*
2. **Showing a chat the inbox's decision live.** Polling the session, or a small
   event stream? This can wait for the first version.

### Settled: questions 1 and 2

**1. Approval doesn't need `RESUMABILITY_ENABLED`.** On ADK, the request processor
(`google/adk/flows/llm_flows/tools/_confirmation.py`) reads the answer from the last
user event in the session and runs or skips the tool. It never looks at
`ResumabilityConfig`. With the setting off:

- one approval, approved or rejected, runs or skips the tool, and the agent replies;
- a run paused before a restart is answered after it, because the session is in the
  session database.

The user guide said approvals depend on the setting. It now says they don't
(`docs/user/tools-and-mcp.md`).

**2. Several confirmations in one turn.** The model asked for `create_shared_block`
twice in one step. Both runtimes emitted two `adk_request_confirmation` calls, one
per tool call.

| Answer | ADK, resumability on | ADK, resumability off | LangGraph |
|---|---|---|---|
| The first only | that tool runs; the model isn't called | that tool runs; the model replies, seeing the second as still waiting | that tool runs; the model isn't called |
| Then the second | that tool runs; the model replies | that tool runs; the model replies again | that tool runs; the model replies |
| Both in one message | both run or are skipped; the model replies once | the same | the same |

So each decision can resume the run on its own, and the inbox doesn't have to
collect them. On every runtime, the answer to a confirmation runs or skips only
the call it names.

- **LangGraph failed this case before.** Any answer with two confirmations pending
  raised `When there are multiple pending interrupts, you must specify the interrupt
  id`, and the session stayed stuck. Fixed alongside this note: the pause carries the
  model's tool call id, each answer resumes the interrupt it names, and the
  confirmation id is `adk-confirm-<tool call id>`, the same each time LangGraph
  re-runs the paused tool (`shared/utils/langgraph/hitl.py`, tests in
  `shared/test/test_langgraph_hitl.py`).
- **ADK with resumability off replies after each decision.** A trigger run there
  would produce a reply after the first decision, with the other call still
  waiting. So the trigger's output should go out only when no request from that
  invocation is still pending. Until then, each reply is kept in `resume_result`.

**Found along the way, for phase 1:**

- **ADK writes a placeholder result for the paused tool.** In the same turn as the
  `adk_request_confirmation` call, it records a function response for the original
  call: `{"error": "This tool call requires confirmation, please approve or
  reject."}`. Capture must not take it for the tool's result. LangGraph emits no
  such response.
- **The ids differ in shape.**
  - ADK: the confirmation call has its own id (`adk-<uuid>`), and `args.originalFunctionCall.id` is the model's tool call id.
  - LangGraph: the confirmation id is `adk-confirm-<tool call id>`, and `args.originalFunctionCall.id` is the model's tool call id.
  - The row keys on the confirmation call id, which is what the answer carries, so both work.
- **LangGraph puts the tool's default arguments in `args`** (`"description": null`
  and so on). The inbox shows them as they are.
- **Only plain-function tools are wrapped on ADK.**
  - `ToolFactory._apply_confirmation_wrapping` skips anything that is already a
    `FunctionTool` and anything that isn't callable.
  - From the code, that would leave MCP tools without approval on ADK. This
    wasn't run.
  - To check before phase 3, whose config editor offers the tools to pick.

### Phase 1, as built

The table, capture on both runtimes, the audit entries for chat decisions, and a
history route (`docs/dev/approvals.md`). Where it differs from the proposal above:

- **Three more columns:**
  - `invocation_id`, so phase 2 can tell whether a run still has a request pending (see question 2);
  - `tool_call_id`, the model's own call id;
  - `decided_via`, which is `chat`, `inbox` or `system`.
- **The rest of the schema:**
  - `(session_id, confirmation_call_id)` is unique;
  - `project_id`, `workspace_id` and `trigger_id` have no foreign keys, as in the log tables.
- **`source` takes the values `response_metrics.resolve_origin` already uses:** `chat`, `api`, `trigger`, `slack` and `eval`.
  - A2A and MCP can't be told apart from a chat yet, which settles the "to check" under [Source](#1-record-each-request).
  - An A2A run's user is `A2A_USER_<context id>`, or the caller when the proxy sends `x-mate-a2a-user`.
  - MATE's MCP servers call agents over A2A with the caller's id.
  - Phase 2 needs a marker on both before "runs with no chat always go to the inbox" can hold for them.
- **The trigger id:** the trigger runner puts it in the new session's state as `mate_trigger_id`, and capture copies it to the row.
  - A trigger run that pauses still returns an empty reply and leaves its row `pending`. That is the behaviour phase 2 changes.
- **The page moved to phase 2.** Phase 1 has the route `GET /dashboard/api/approvals`.
  - The Approvals page, with Waiting and History, is built in phase 2.
  - It uses the components of the dashboard redesign (`documents/design/dashboard-2.1.md`), whose step 2 comes before it.

### Phase 2, as built

The inbox (`docs/dev/approvals.md`): deciding, resuming, triggers end to end, expiry,
cancellation with the agent, and the Approvals page. Where it differs from the
proposal above:

- **Who decides:** admins of the request's workspace, through `POST /dashboard/api/approvals/{id}/decide`. The `approver` role is still open question 1.
- **A chat's request is answered in the chat; the inbox answers 409.**
  - This holds until the `approver` setting of phase 3 lets the inbox take a chat's request.
  - Taking one before then could run the tool twice: once from the inbox, and again from a late answer on the card.
- **A2A and MCP runs are told apart now.**
  - The agent server's `/a2a/` middleware sets a context variable that the capture plugin reads: `a2a`, or `mcp` when MATE's MCP bridge sends `x-mate-surface: mcp`.
  - The proxy drops that header from what clients send.
- **A trigger waits for its whole session.**
  - Its output goes out when no request in the firing's session is pending, not per invocation.
  - A resumed run that asks again starts a new invocation in the same session, and the output still waits for it.
  - The firing records `awaiting_approval` until then. The final result carries `"after_approval": true`.
- **Expiry covers the inbox's requests only.** A chat's request has someone who may still answer it. The wait is `APPROVALS_EXPIRE_MINUTES` (default 1440); the per-agent `expires_after_minutes` is phase 3's.
- **Cancellation covers deleting an agent, not a session.**
  - The rows are cancelled in the deleting transaction, and audited once it commits.
  - A deleted session's pending request fails when resumed, with `resume_status` `error`.
- **Resuming runs in a thread of the auth server.** A restart mid-resume leaves the row `resuming`.
- **The page** (2b) lists in Waiting only the requests the inbox decides; a chat's waiting request is in History, marked "waiting in the chat".
  - A row opens a dialog with the arguments, rather than showing them in the list.
  - The session link opens Session Tracking's inspector, as the run's user may not be the admin.
  - ADK's default hint, which is addressed to a client, is not shown.

### Phase 3, as built

In two parts. 3a is the settings and what they do; 3b is their editor in the Visual
Builder. See `docs/dev/approvals.md`. Where 3a differs from the proposal above:

- **Who decides in the inbox:** admins of the workspace, as in phase 2 (open question 1).
- **Answering once.** Every answer on its way to a run passes `approval_service.gate_answer` on both runtimes:
  - it is passed on only if whoever sent it may answer;
  - only once (`answered_at`, V051);
  - and, after an inbox decision, as decided.
  - This is what lets the inbox take a chat's request without running the tool twice. It also stops an API or A2A caller answering its own request.
- **What the chat shows** comes from the server: the confirmation call's args get `"mateApprover": "inbox"` or `"either"`, and the chat clients read it.
- **The default destination** (`APPROVALS_NOTIFY_*`) is the Default workspace's only, so another workspace's requests never reach the platform's channel.
- **Notifications carry the arguments' names, not their values.**
- **The link** is absolute when `MATE_PUBLIC_URL` is set, else a path.
- **Alerts and approvals share `notify.deliver`,** moved out of `AlertService._deliver`.
- **3b, the editor,** is a dialog of its own (`static/js/approval-settings.js`), opened from the Visual Builder's agent panel, so the agent page of the dashboard redesign can open it too.

### Phase 4, as built: the security review

A code-review at `high` over `2dd6826..2c3e648` (phases 1 to 3b). Each finding was checked by hand, and each fix has a test that fails when the fix is reverted.

**Fixed:**
- **An answer could be read two ways.** ADK also accepts `{"response": "<json>"}` and reads `"false"` as no; the plugin read only `confirmed`, with Python's `bool`.
  - A forged wrapper could therefore run a tool the inbox had rejected.
  - Both runtimes now read answers as ADK does (`answer_confirmed`), and the ADK plugin passes on only a plain `{"confirmed": ...}`.
- **The check failed open.** With no row, or no database, `gate_answer` passed the chat's answer on, and the plugin passed the message on when it raised. Both now fail closed.
- **A resumed A2A or MCP run** recorded its next request as a chat's, which nobody could answer. A request now takes the A2A or MCP source of the session's earlier rows.
- **A trigger could send its output twice** when two requests of one session finished resuming together. Delivery now waits for requests still being resumed, under a lock.
- **Expiry resumed one run after another** inside the minute's job. Each now resumes in a thread.
- **`notify.deliver` raised on a malformed `timeout`.**
- **Missing type hints** in `approval_service` and `run_surface`.

**Not taken:** "a dynamic sub-agent takes its root's settings". Such an agent runs in a `Runner` of its own, without the plugin, so its request never reaches a chat or the inbox, and the tool doesn't run.

## Phases

1. **Table, capture and history.** The table, the plugin and translator hooks, the
   history route, and audit entries for decisions made in the chat. No behaviour
   changes. Built.
2. **The inbox.** The Approvals page with the Waiting and History tabs, approve and
   reject, resuming, triggers end-to-end, and expiry.
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
