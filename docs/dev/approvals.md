---
title: Approval requests
summary: How a require_confirmation pause is recorded, decided in a chat or in the inbox, resumed, expired and cancelled, on both runtimes.
audience: dev
order: 65
covers:
  - shared/utils/approval_service.py
  - shared/utils/approval_resume.py
  - shared/utils/run_surface.py
  - shared/callbacks/approval_plugin.py
  - shared/sql/migrations/*/V050__approval_requests.sql
---

# Approval requests

An agent with `require_confirmation` on a tool pauses before the tool runs and
emits an `adk_request_confirmation` function call. The run continues when a
function response with the same id comes back on the same session. The user guide
explains what the person chatting sees ([Tools and MCP servers](../user/tools-and-mcp.md)).

Each pause is recorded as a row of `approval_requests` (migration V050)
(`documents/design/approvals-inbox.md`). Who answers depends on where the run came
from:

- **A chat** (`source` `chat`): the person chatting answers on the card, as before;
  the row records the answer.
- **Any other run** (a trigger, the OpenAI-compatible API, Slack, an eval, A2A, MCP):
  nobody is there to answer, so the request waits in the inbox. An admin decides it
  with `POST /dashboard/api/approvals/{id}/decide`, the auth server sends the
  answer to the paused run, and a trigger's output goes out once it has finished.
  A request nobody decides expires.

The dashboard has no page for the inbox yet; it comes with the dashboard
redesign's components.

## Where rows are written

The agent runtime writes the rows, not the auth server. A2A and MCP calls, triggers,
Slack and the OpenAI-compatible API reach the agent process without going through
the auth server's proxy, and the runtime sees them all.

| Runtime | The pause | The answer |
|---|---|---|
| ADK | `ApprovalCapturePlugin.on_event_callback` (`shared/callbacks/approval_plugin.py`), for each `adk_request_confirmation` call in an event | `ApprovalCapturePlugin.on_user_message_callback`, for each `adk_request_confirmation` function response in the user's message |
| LangGraph | `_record_requests` in `shared/utils/langgraph/executor.py`, as each complete event is stored | `_record_decisions`, for the answers the graph resumes on. An answer naming no paused call gets "This conversation is not waiting on that approval." and is not recorded |

**On ADK:**
- The plugin is registered on every App, whatever `MATE_PLUGINS_ENABLED` says, as `MetricsPlugin` is (`create_app_with_context_caching` in `shared/utils/utils.py`).
- It returns `None` from both hooks, so it never changes an event or a message.
- The database writes run in a thread (`asyncio.to_thread`).

**On both runtimes:** the calls go to `shared/utils/approval_service.py`, so a row is the same whichever runtime wrote it. Its functions log and return on failure; they never raise into a run.

## The row

| Column | From |
|---|---|
| `confirmation_call_id` | the id of the `adk_request_confirmation` call, which the answer carries. ADK makes it `adk-<uuid>`; the LangGraph runtime makes it `adk-confirm-<tool call id>` |
| `tool_call_id`, `tool_name`, `tool_args`, `hint` | the call's `originalFunctionCall` and `toolConfirmation` (snake_case is read too) |
| `agent_name` | the event's author: the agent that asked, which may be a sub-agent |
| `app_name`, `user_id`, `session_id`, `invocation_id` | the run |
| `project_id`, `workspace_id` | the agent's, copied when the row is written (`log_projects.scope_of_agent`). An agent the database doesn't know, such as a hardcoded sub-agent, takes its root's |
| `source` | `a2a` or `mcp` from `run_surface` (below); otherwise `response_metrics.resolve_origin`: `trigger` for user `trigger_runner`, `eval` for `eval_runner`, `api` for sessions starting `openai_sess_`, `slack` for `slack_`, else `chat` |
| `trigger_id` | the session state key `mate_trigger_id`, which `TriggerRunner._invoke_agent` sets when it creates the session |
| `status` | `pending`, then `approved`, `rejected`, `expired` or `cancelled` |
| `expires_at` | `requested_at` plus `APPROVALS_EXPIRE_MINUTES` (default 1440) |
| `decided_by`, `decided_via`, `decided_at`, `note` | who decided and how: the run's user and `chat`; the admin, `inbox` and their note; or `system` for an expiry or a cancellation |
| `resume_status`, `resume_result` | after an inbox decision or an expiry: `resuming`, then `ok` with the run's reply (cut at 4,000 characters) or `error` with the failure |

- **Arguments are capped.** They are stored as JSON, cut at 16,000 characters (`MAX_ARGS_CHARS`); a cut value ends in `…` and is no longer valid JSON.
- **A2A and MCP runs** carry no marker in their ids: an A2A run's user is `A2A_USER_<context id>`, or the caller when the auth server's proxy sends `x-mate-a2a-user`, and MATE's MCP servers call agents over A2A with the caller's id.
  - The agent server's `/a2a/` middleware (`CallerAsA2AUser` in `shared/utils/a2a_routes.py`) sets a context variable for the request (`shared/utils/run_surface.py`): `mcp` when the request has `x-mate-surface: mcp`, which MATE's MCP bridge sends, else `a2a`.
  - The plugin reads it. The auth server's proxy drops `x-mate-surface` from what clients send.
  - Only the ADK runtime serves A2A.
- **Unique pair.** `(session_id, confirmation_call_id)` is unique: a LangGraph confirmation id comes from the model's tool call id, which is only unique within a conversation. Writing the same call again returns the first row's id.
- **No foreign keys.** `project_id`, `workspace_id` and `trigger_id` have none, as in the log tables, so the history stays when an agent, project or trigger is deleted.

## Decisions and the audit log

A chat's answer goes through `record_decision`, an inbox decision through
`claim_decision`. Both change the row only while it is pending:
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
  - **409** for a chat's request, which the chat answers;
  - **409** for a request that is no longer pending: of two clicks, one wins, and the second sends nothing.
- **On 200** the row is decided and audited, `resume_status` is `resuming`, and the run is resumed in a thread (`approval_resume.resume_in_background`).

## Resuming the run

`approval_resume.resume` posts to the agent server's `/run_sse` on the row's
`app_name`, `user_id` and `session_id`, as the trigger runner posts a prompt. The new
message is a function response named `adk_request_confirmation`, with the row's
`confirmation_call_id` and `{"confirmed": true|false}`. Both runtimes take it as they
take the chat's answer.

- **Reading the reply:** the run's reply is read with `trigger_runner.collect_reply` and stored in `resume_result`; a failure is stored as `error`.
- **A trigger's run:** its reply goes through the trigger's output (`TriggerRunner.deliver_after_approval`), and becomes the trigger's last result with `"after_approval": true`. This happens only when no request in its session is still pending, so several requests in one run send one output. A run that asks again while resumed waits again.
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
- has its run resumed with `{"confirmed": false}`, so the agent is told no and a trigger's output still goes out.

A chat's request doesn't expire: the person chatting may still answer it.

**Cancellation.** Deleting an agent cancels the pending requests it asked or whose
run it is the root of:
- `agent_cleanup.delete_agent_rows` calls `cancel_in_transaction`, in the deleting transaction;
- the `approval.cancelled` entries are written once that transaction commits, so a rolled-back deletion leaves none;
- nothing is resumed.

## The history route

`GET /dashboard/api/approvals` lists the rows, newest first.
- **Filters:** `status`, `agent_name` and `source`, with `limit` (at most 500) and `offset`.
- **Scope:** the caller's workspace, through `rows_of`. A row with no workspace counts as the default workspace's.
- **`all_workspaces=true`:** every workspace, for a platform administrator.
- **Admins only:** as every `/dashboard/api/` route outside the allowlists.
- **Rows carry the arguments,** which may hold personal data.

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

`shared/test/test_cross_workspace.py` checks both routes against another
workspace's row, and that a workspace decides its own.
