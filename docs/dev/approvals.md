---
title: Approval requests
summary: How a require_confirmation pause is recorded, answered once by whoever may answer it, resumed, announced, expired and cancelled, on both runtimes.
audience: dev
order: 65
covers:
  - shared/utils/approval_service.py
  - shared/utils/approval_resume.py
  - shared/utils/run_surface.py
  - shared/callbacks/approval_plugin.py
  - shared/utils/approval_config.py
  - static/js/approval-settings.js
  - templates/dashboard/modals/approval_settings_modal.html
  - shared/utils/approval_notify.py
  - shared/utils/notify.py
  - shared/sql/migrations/*/V050__approval_requests.sql
  - shared/sql/migrations/*/V051__approval_approver.sql
  - templates/dashboard/approvals.html
  - static/js/approvals-page.js
---

# Approval requests

An agent with `require_confirmation` on a tool pauses before the tool runs and
emits an `adk_request_confirmation` function call. The run continues when a
function response with the same id comes back on the same session. The user guide
explains what the person chatting sees ([Tools and MCP servers](../user/tools-and-mcp.md)).

Each pause is recorded as a row of `approval_requests` (migrations V050 and V051)
(`documents/design/approvals-inbox.md`). Who answers depends on where the run came
from and on the agent's `approver` setting (below):

- **A chat** (`source` `chat`), with `approver` `chat` (the default): the person
  chatting answers on the card; the row records the answer.
- **A chat with `approver` `inbox`:** the card has no buttons, and only an admin
  answers, in the inbox. With `either`, both may, and the first answer wins.
- **Any other run** (a trigger, the OpenAI-compatible API, Slack, an eval, A2A, MCP):
  nobody is there to answer, so the request waits in the inbox, whatever `approver` says.

An admin decides an inbox request on the Approvals page or with
`POST /dashboard/api/approvals/{id}/decide`. The auth server then sends the answer
to the paused run, and a trigger's output goes out once it has finished. A request
nobody decides expires. A request the inbox answers is announced to the agent's
`notify` destination.

## The agent's settings

`require_confirmation` in the agent's `tool_config` is a list of tool names, or an
object (`shared/utils/approval_config.py`):

```json
{"require_confirmation": {
    "tools": ["issue_refund"],
    "approver": "inbox",
    "notify": {"type": "slack", "url": "https://hooks.slack.com/..."},
    "expires_after_minutes": 1440
}}
```

| Field | Meaning |
|---|---|
| `tools` | the tools that ask first; the list form is this alone |
| `approver` | `chat` (default), `inbox` or `either`: who answers a chat's request |
| `notify` | `{"type": "http" \| "email" \| "slack" \| "discord", "url" or "to", ...}`, as an alert rule's destination; shown masked, as that is |
| `expires_after_minutes` | 1 to 129,600; else `APPROVALS_EXPIRE_MINUTES` |

- **Both runtimes read `tools` through `approval_config.parse`:** `ToolFactory._apply_confirmation_wrapping` on ADK, and `AgentBuilder._build_tools` on LangGraph. A value that doesn't validate is read with defaults, so an older agent still loads.
- **Saving checks it.** Creating, updating and importing an agent answer 400 with `approval_config.validate`'s messages (`_refuse_bad_approval_config`).
- **The editor** is `static/js/approval-settings.js` with its dialog in `templates/dashboard/modals/approval_settings_modal.html`, opened from the Visual Builder's agent panel (**Approvals → Configure**).
  - `mateApprovalSettings.open(agent, onSave)` reads the agent's `tool_config`, and `write` puts the settings back into it, keeping its other keys.
  - It writes the list when only tools are set, and the object otherwise.
  - No tools removes the entry.
  - It checks what `validate` checks before calling `onSave`, which saves the agent and resolves to `true`. On anything else the dialog stays open.
  - `summary` is the line the panel shows.
  - A notify destination keeps the fields the dialog doesn't show (`headers`, `timeout`, `subject`) while its type stays the same.
- **The row keeps the settings of its time.** `record_request` copies `approver`, and sets `expires_at` from `expires_after_minutes`. It reads the agent that asked, else the run's root, as for the scope. A later change of the agent doesn't move requests already waiting.
- **Agents the database doesn't know:**
  - A hardcoded sub-agent takes its root's settings.
  - A sub-agent built by `subagent_delegation` runs in a `Runner` of its own, without the plugin, and its events never reach the chat. A tool it may only run with approval is therefore never approved: the call ends with ADK's "requires confirmation" error.

## Where rows are written

The agent runtime writes the rows, not the auth server. A2A and MCP calls, triggers,
Slack and the OpenAI-compatible API reach the agent process without going through
the auth server's proxy, and the runtime sees them all.

| Runtime | The pause | The answer |
|---|---|---|
| ADK | `ApprovalCapturePlugin.on_event_callback` (`shared/callbacks/approval_plugin.py`), for each `adk_request_confirmation` call in an event | `ApprovalCapturePlugin.on_user_message_callback`, for each `adk_request_confirmation` function response in the user's message |
| LangGraph | `_record_requests` in `shared/utils/langgraph/executor.py`, before each complete event is stored | `_gate_decisions`, for the answers to calls the graph is paused on. An answer naming no paused call gets "This conversation is not waiting on that approval."; one the gate takes out, "This approval is decided in the approvals inbox, or was answered already." |

**On ADK:**
- The plugin is registered on every App, whatever `MATE_PLUGINS_ENABLED` says, as `MetricsPlugin` is (`create_app_with_context_caching` in `shared/utils/utils.py`).
- `on_event_callback` returns `None`, and changes the event in place: a call the chat may not answer gets `"mateApprover"` in its args (below), before the runner stores and streams it.
- `on_user_message_callback` returns the message with each answer as the gate allows, and leaves a message with no answer in it alone.
  - An answer that goes on is rewritten as a plain `{"confirmed": ...}`, so ADK reads exactly what the gate allowed.
  - Any other answer is taken out. A message left with no part gets a text part saying an answer was not passed on, so the run still has a message.
  - If the check itself fails, every answer is taken out.
- The database writes run in a thread (`asyncio.to_thread`).

**On both runtimes:** the calls go to `shared/utils/approval_service.py`, so a row is the same whichever runtime wrote it. Its functions log and return on failure; they never raise into a run.

## The row

| Column | From |
|---|---|
| `confirmation_call_id` | the id of the `adk_request_confirmation` call, which the answer carries. ADK makes it `adk-<uuid>`; the LangGraph runtime makes it `adk-confirm-<hash>` of the tool call id, the tool and its arguments, so two calls the model gave one id are two requests |
| `tool_call_id` (at most 255 characters), `tool_name`, `tool_args`, `hint` | the call's `originalFunctionCall` and `toolConfirmation` (snake_case is read too) |
| `agent_name` | the event's author: the agent that asked, which may be a sub-agent |
| `app_name`, `user_id`, `session_id`, `invocation_id` | the run |
| `project_id`, `workspace_id` | the agent's, copied when the row is written (`log_projects.scope_of_agent`). An agent the database doesn't know, such as a hardcoded sub-agent, takes its root's |
| `source` | `a2a` or `mcp` from `run_surface` (below); else the source of an earlier `a2a` or `mcp` row in the same session, since the inbox resumes such a run over `/run_sse` where no surface is set; otherwise `response_metrics.resolve_origin`: `trigger` for user `trigger_runner`, `eval` for `eval_runner`, `api` for sessions starting `openai_sess_`, `slack` for `slack_`, else `chat` |
| `trigger_id` | the session state key `mate_trigger_id`, which `TriggerRunner._invoke_agent` sets when it creates the session |
| `status` | `pending`, then `approved`, `rejected`, `expired` or `cancelled` |
| `approver` | the agent's `approver` when the row was written (V051); `NULL` in rows from before means `chat` |
| `expires_at` | `requested_at` plus the agent's `expires_after_minutes`, else `APPROVALS_EXPIRE_MINUTES` (default 1440) |
| `answered_at` | when an answer reached the run (V051). Rows decided before V051 take their `decided_at` |
| `decided_by`, `decided_via`, `decided_at`, `note` | who decided and how: the run's user and `chat`; the admin, `inbox` and their note; or `system` for an expiry or a cancellation |
| `resume_status`, `resume_result` | after an inbox decision or an expiry: `resuming`, then `ok` with the run's reply (cut at 4,000 characters) or `error` with the failure |

- **Arguments are capped.** They are stored as JSON, cut at 16,000 characters (`MAX_ARGS_CHARS`); a cut value ends in `…` and is no longer valid JSON.
- **A2A and MCP runs** carry no marker in their ids: an A2A run's user is `A2A_USER_<context id>`, or the caller when the auth server's proxy sends `x-mate-a2a-user`, and MATE's MCP servers call agents over A2A without it, in a context made from the caller when the call has a `session_id`.
  - The agent server's `/a2a/` middleware (`CallerAsA2AUser` in `shared/utils/a2a_routes.py`) sets a context variable for the request (`shared/utils/run_surface.py`): `mcp` when the request has `x-mate-surface: mcp`, which MATE's MCP bridge sends, else `a2a`.
  - The plugin reads it. The auth server's proxy drops `x-mate-surface` from what clients send.
  - Only the ADK runtime serves A2A.
- **Unique pair.** `(session_id, confirmation_call_id)` is unique: a LangGraph confirmation id comes from the model's tool call id, which is only unique within a conversation. Writing the same call again returns the first row's id.
- **No foreign keys.** `project_id`, `workspace_id` and `trigger_id` have none, as in the log tables, so the history stays when an agent, project or trigger is deleted.

## Who may answer, and answering once

Two predicates in `approval_service` say who may answer a row:

- **`chat_may_answer(source, approver)`:** the source is `chat` and `approver` isn't `inbox`.
- **`inbox_decides(source, approver)`:** the source isn't `chat`, or `approver` is `inbox` or `either`. `inbox_clause()` is the same as a filter. The Waiting tab (`inbox=true`), `claim_decision` and expiry use it.

**What the chat shows.** `answered_in(source, approver)` gives `inbox` when the chat
may not answer, and `either` when both may. Both runtimes put that value in the call's
args as `"mateApprover"`, in the stored event and the streamed one. The chat clients
(`static/js/widget/chat.js`, `static/js/standalone/chat.js`, which the Work Room uses)
then draw the card:
- with `inbox`, without buttons, saying an administrator decides;
- with `either`, with buttons, adding that an administrator may decide too.

ADK keeps the extra key in the args, and reads only `originalFunctionCall` and
`toolConfirmation` when the answer comes.

**The gate.** Every answer on its way to a run passes
`gate_answer(app_name, user_id, session_id, confirmation_call_id, confirmed)`. A row is
found only for the run that made it: the same app, user and session.

| The row | The run receives |
|---|---|
| none, or the database can't be read | nothing: without the row nobody can tell whether the chat may answer |
| pending, and the chat may answer | the answer, which decides the row as `record_decision` does |
| pending, and only the inbox may answer | nothing |
| approved, rejected or expired, not yet answered | the decision, whatever the answer said. This is how the inbox's resume reaches the run |
| answered already, or cancelled | nothing |

**Reading the answer.** `answer_confirmed` reads an answer as ADK does
(`ToolConfirmation.from_response_dict`): `{"confirmed": ...}`, or that wrapped as
`{"response": <dict or JSON text>}`, with pydantic's reading of booleans, so
`"false"` is no. An answer it can't read is a rejection. The ADK plugin and the
LangGraph runtime (`hitl.extract_confirmation_responses`) both use it.

**Marking it answered.** "Answered" is `answered_at`, set by a conditional update
(`... WHERE answered_at IS NULL`), or with the decision itself for a chat's answer.
So the tool runs at most once, and only as decided:
- a second click on the card is dropped;
- so is a late or forged answer after the inbox decided;
- an answer from a client to a run nobody chats in (an API caller answering its own request) is dropped too.

If a chat's answer reaches a decided row before the inbox's own resume does, the
run gets the inbox's decision from it, and the resume's message is the one dropped.

## Notifications

When `record_request` writes a row that `inbox_decides`, it starts
`approval_notify.send_in_background` (`shared/utils/approval_notify.py`), in a thread
of its own:

- **The destination** is the agent's `notify`. Without one, an agent of the default
  workspace uses `APPROVALS_NOTIFY_TYPE` with `APPROVALS_NOTIFY_URL` or
  `APPROVALS_NOTIFY_TO`. Other workspaces get none from the environment, so their
  requests never reach the platform's channel.
- **Delivery** is `notify.deliver`, which alert rules use too:
  - http gets the payload as JSON;
  - email gets the text, then the payload's fields;
  - Slack and Discord get the text in the body their webhooks take.
  - The outbound guard and the workspace's email permission apply, as for alerts.
- **The payload:** `event` `approval.requested`, `message`, `request_id`, `agent`,
  `tool`, `source`, `arguments` (the argument names, never their values),
  `expires_at` and `link`.
- **`link`** is `/dashboard/approvals?id=<id>`, under `MATE_PUBLIC_URL` when that is
  set. The page opens that request.
- **A failure** is logged and never reaches the run.

## Decisions and the audit log

A chat's answer goes through `record_decision` (from the gate), an inbox decision
through `claim_decision`. Both change the row only while it is pending:
`UPDATE ... WHERE session_id = ? AND confirmation_call_id = ? AND status = 'pending'`.
A second answer to the same call changes nothing and writes no audit entry.

When the row changes, it writes one audit entry:

| Field | Value |
|---|---|
| `action` | `approval.approved`, `approval.rejected`, `approval.expired` or `approval.cancelled` |
| `actor` | `decided_by` |
| `resource_type` | `approval_request` |
| `resource_id` | the row id |
| `details` | `agent`, `tool`, `source`, `decided_via` and `args_sha256`, the SHA-256 of the arguments as stored; the inbox's `note`; `reason` for a cancellation. The arguments themselves stay in the row |
| `project_id`, `workspace_id` | the row's |

## Deciding in the inbox

`POST /dashboard/api/approvals/{approval_id}/decide`:
- **Body:** `{"decision": "approve" | "reject", "note": "..."}`; the note is optional and cut at 2,000 characters.
- **Who:** admins only, like every `/dashboard/api/` route outside the allowlists.
- **Answers, in this order:**
  - **404** for an id that isn't in the caller's workspace, whatever the body says;
  - **400** for a decision other than those two, or a note that isn't text;
  - **409** for a request only the chat answers (`inbox_decides` is false);
  - **409** for a request that is no longer pending: of two clicks, one wins, and the second sends nothing.
- **On 200** the row is decided and audited, `resume_status` is `resuming`, and the run is resumed in a thread (`approval_resume.resume_in_background`).

## Resuming the run

`approval_resume.resume` posts to the agent server's `/run_sse` on the row's
`app_name`, `user_id` and `session_id`, as the trigger runner posts a prompt. The new
message is a function response named `adk_request_confirmation`, with the row's
`confirmation_call_id` and `{"confirmed": true|false}`. Both runtimes take it as they
take the chat's answer.

- **Reading the reply:** the run's reply is read with `trigger_runner.collect_reply` and stored in `resume_result`; a failure is stored as `error`.
- **A trigger's run:** its reply goes through the trigger's output (`TriggerRunner.deliver_after_approval`), and becomes the trigger's last result with `"after_approval": true`. This happens only when no request in its session is pending or still being resumed (`waiting_in_session`), so several requests in one run send one output.
  - Storing the outcome and that check happen under one lock (`_FINISH_LOCK`). Of two resumes of one session finishing together, only the last routes the output.
  - Every resume runs in a thread of the auth server.
  - A run that asks again while resumed waits again.
- **Any other run:** its reply stays in its session.
- **A restart mid-resume:** an auth server restarted while a run is being resumed leaves the row `resuming`.
- **A renamed agent:** its pending requests still name the old agent, so resuming them fails with `error`.

## A trigger waits

`TriggerRunner._invoke_agent` returns an `AgentReply`: the reply text, with the
`session_id` it was given in. `_execute_trigger_sync` looks up the requests pending
in that session:
- **none:** the output is routed as before;
- **any:** the firing returns `status: "awaiting_approval"`, with the session and the request ids, and routes nothing.

The Triggers page shows such a firing with an hourglass.

## Expiry and cancellation

**Expiry.** `approval_resume.expire_and_resume` runs every minute on the trigger
runner's scheduler (`approval_expiry`). An inbox request past `expires_at`:
- becomes `expired`, by `system`, with an `approval.expired` entry;
- has its run resumed with `{"confirmed": false}`, in a thread of its own (`resume_in_background`), so the agent is told no and a trigger's output still goes out, and a slow agent doesn't hold up the next pass.

A request only the chat answers doesn't expire: the person chatting may still answer it.

**Cancellation.** Deleting an agent cancels the pending requests it asked or whose
run it is the root of:
- `agent_cleanup.delete_agent_rows` calls `cancel_in_transaction`, in the deleting transaction;
- the `approval.cancelled` entries are written once that transaction commits, so a rolled-back deletion leaves none;
- nothing is resumed.

## The history route

`GET /dashboard/api/approvals` lists the rows, newest first.
- **Filters:** `status`, `agent_name` and `source`, with `limit` (at most 500) and `offset`.
- **`inbox=true`** keeps the requests the inbox decides (`inbox_clause()`).
- **`id`** picks one request; the Approvals page uses it for `?id=` links.
- **`q`** matches the agent, the tool and the user who started the run, case-insensitively; with several words, a row must match each.
- **Scope:** the caller's workspace, through `rows_of`. A row with no workspace counts as the default workspace's.
- **`all_workspaces=true`:** every workspace, for a platform administrator.
- **Admins only:** as every `/dashboard/api/` route outside the allowlists.
- **Rows carry the arguments,** which may hold personal data.

## The page

`/dashboard/approvals` (`templates/dashboard/approvals.html`, `static/js/approvals-page.js`), for admins, is built on the
[dashboard components](dashboard-components.md):

- **Waiting** loads `?status=pending&inbox=true&limit=500` into a client-side table, and again every 30 seconds while the tab is shown, the page is visible and no request is open.
- **History** is a table the API pages (`offset`, `limit`, `q`, `status`, `source`).
- **A row** opens a dialog with the arguments, the hint and, once decided, `resume_result`. Approve and Reject post to the decide route with the note.
  - ADK's default hint, which tells a client how to answer, is not shown.
  - **Open the session** links to `/dashboard/sessions?session_id=…&app_name=…&user_id=…`, which opens that session in the Session Tracking inspector.
- **The sidebar's count** beside Approvals is the `total` of `?status=pending&inbox=true&limit=1` (`refreshApprovalsCount` in `static/js/dashboard-nav.js`). It is fetched on every page that has the Control Room sidebar, and again after a decision.

## Tests

`shared/test/test_approval_requests.py` covers:
- both argument casings;
- the cap;
- the row's fields and source;
- writing the same call twice;
- a decision made once, twice, and for an unknown call;
- the audit entry;
- both runtimes' hooks;
- the trigger id in the session;
- V050 on SQLite.

`shared/test/test_approvals_inbox.py` covers:
- deciding in the inbox: once, twice, a chat's request, another workspace's, a bad body;
- resuming: the message, a trigger's delivery, waiting for the last request, a failure;
- a trigger that waits;
- expiry;
- cancellation, committed and rolled back;
- the A2A and MCP surface.

`shared/test/test_approvals_phase3.py` covers:
- the settings: both forms, defaults for what doesn't validate, validation, a save refused;
- recording: the approver and expiry kept on the row, a sub-agent taking its root's, the notification and what it leaves out;
- the gate: the chat answering once, the chat refused where the inbox answers, the inbox's decision delivered once and as decided, a cancelled row, an unknown call;
- the inbox deciding a chat's request by `approver`, and expiry following `inbox_decides`;
- the ADK plugin: `mateApprover`, an answer taken out, an answer replaced;
- (in `shared/test/js/approval_settings.test.js`) the editor's reading, writing, validation and summary;
- notifications: the default destination, the message, nowhere to send.

`shared/test/test_cross_workspace.py` checks both routes against another
workspace's row, that a workspace decides its own, and the list's `inbox` and `q`
filters.
