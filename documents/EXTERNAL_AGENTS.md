# Pointing MATE at an agent you already run

MATE's control layer — RBAC, cost tracking, guardrails, evals, versioning, audit
logs, the embeddable widget — normally applies to agents built inside MATE. This
lets it apply to an agent running somewhere else, without rebuilding it.

## How it works

An agent reached over `POST /v1/chat/completions` is, on the wire, the same shape
as a model. So an external agent is registered as an ordinary MATE agent whose
model points at the remote endpoint. Nothing else about the agent is special: it
sits in the tree, obeys RBAC, is covered by guardrails, can be evaluated, and
appears in usage and audit exactly like any other agent.

Requirements for the remote:

- It speaks the OpenAI chat-completions API.
- It is reachable from the MATE host.

## Configuring it

In the agent modal, or via `POST /dashboard/api/agents`:

| Field | Example | Notes |
|-------|---------|-------|
| Model Name | `openai/my-agent` | **Needs a provider prefix.** A bare `gemini-*` name routes to the native Gemini backend, which ignores the base URL |
| Model Base URL | `https://agent.example.com/v1` | The remote's OpenAI-compatible root |
| Model API Key | `${MY_AGENT_KEY}` | Optional. See below |

Leave both endpoint fields empty and the agent behaves exactly as before,
resolving its provider from environment variables.

## Secrets

Write the key as `${VAR}` to read it from the server environment. A key typed
literally is stored in the `agents_config` row and captured in that agent's
version history, and is therefore visible to anyone who can read the database or
export the configuration.

The dashboard never sends a stored literal key to the browser — it is replaced by
a sentinel, and saving the form with the sentinel unchanged leaves the stored
value alone. A `${VAR}` reference is not a secret, so it is shown as written.

Two failure modes are deliberate:

- **A `${VAR}` that is not set refuses to build the agent**, with the missing name
  in the error and in the dashboard's last-error field. The alternative is worse:
  LiteLLM would fall back to the provider's own key and send it to whatever host
  the base URL names.
- **An endpoint configured with no key does not inherit the provider's key.** A
  placeholder is sent instead, so `OPENAI_API_KEY` never travels to a third-party
  host. Endpoints that need no key (local servers, private networks) work as
  expected.

## What you get, and what you do not

Applies to the external agent as to any other:

- Per-agent RBAC, so the remote is only reachable by the roles you allow
- Input and output guardrails
- Eval suites, including LLM-as-judge and regression thresholds
- Configuration versioning and rollback, and audit entries for every change
- The chat widget and the Work Room

**Cost tracking depends on the remote.** Token counts come from the `usage`
object in its response. An endpoint that omits `usage` produces requests with no
token counts rather than invented ones — the request is still recorded.

The remote runs its own agent loop. MATE sees one request and one response, so
its internal tool calls and sub-agent steps do not appear in MATE's traces.

## Both runtimes

The ADK and LangGraph runtimes resolve the endpoint through the same function
(`resolve_agent_endpoint` in `shared/utils/utils.py`), so a configuration means
the same thing under either.
