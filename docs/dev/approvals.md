---
title: Approval requests
summary: How a require_confirmation pause and its answer are recorded in approval_requests, on both runtimes and from every surface.
audience: dev
order: 65
covers:
  - shared/utils/approval_service.py
  - shared/callbacks/approval_plugin.py
  - shared/sql/migrations/*/V050__approval_requests.sql
---

# Approval requests

An agent with `require_confirmation` on a tool pauses before the tool runs and
emits an `adk_request_confirmation` function call. The run continues when a
function response with the same id comes back on the same session. The user guide
explains what the person chatting sees ([Tools and MCP servers](../user/tools-and-mcp.md)).

Each pause is recorded as a row of `approval_requests` (migration V050), and each
answer marks the row decided. This is phase 1 of the approvals inbox
(`documents/design/approvals-inbox.md`). It only records:
- nothing reads the table to change a run;
- there is no page yet;
- only a chat can answer.

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
| `source` | `response_metrics.resolve_origin`: `trigger` for user `trigger_runner`, `eval` for `eval_runner`, `api` for sessions starting `openai_sess_`, `slack` for `slack_`, else `chat` |
| `trigger_id` | the session state key `mate_trigger_id`, which `TriggerRunner._invoke_agent` sets when it creates the session |
| `status` | `pending`, then `approved` or `rejected`. `expired` and `cancelled` are kept for later phases |
| `decided_by`, `decided_via`, `decided_at` | the user of the run that carried the answer, and `chat` |

- **Arguments are capped.** They are stored as JSON, cut at 16,000 characters (`MAX_ARGS_CHARS`); a cut value ends in `…` and is no longer valid JSON.
- **A2A and MCP runs count as `chat`.** An A2A run's user is `A2A_USER_<context id>`, or the caller when the auth server's proxy sends `x-mate-a2a-user`. MATE's MCP servers call agents over A2A with the caller's id. Neither has a marker of its own yet.
- **Unique pair.** `(session_id, confirmation_call_id)` is unique: a LangGraph confirmation id comes from the model's tool call id, which is only unique within a conversation. Writing the same call again returns the first row's id.
- **No foreign keys.** `project_id`, `workspace_id` and `trigger_id` have none, as in the log tables, so the history stays when an agent, project or trigger is deleted.

## Decisions and the audit log

`record_decision` changes the row only while it is pending:
`UPDATE ... WHERE session_id = ? AND confirmation_call_id = ? AND status = 'pending'`.
A second answer to the same call changes nothing and writes no audit entry.

When the row changes, it writes one audit entry:

| Field | Value |
|---|---|
| `action` | `approval.approved` or `approval.rejected` |
| `actor` | `decided_by` |
| `resource_type` | `approval_request` |
| `resource_id` | the row id |
| `details` | `agent`, `tool`, `source`, `decided_via` and `args_sha256`, the SHA-256 of the arguments as stored. The arguments themselves stay in the row |
| `project_id`, `workspace_id` | the row's |

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

`shared/test/test_cross_workspace.py` checks the history route against another workspace's row.
