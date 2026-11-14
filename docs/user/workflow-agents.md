---
title: "Workflow (graph) agents"
summary: Route between sub-agents with a graph: conditional edges, routers, retries and approval.
audience: user
order: 25
covers:
  - shared/utils/agent_manager.py::GraphWorkflow
  - shared/utils/agent_manager.py::_initialize_agent
  - shared/utils/tools/tool_factory.py::_apply_confirmation_wrapping
  - static/js/agents-visual-builder.js
---

# Workflow Agents (ADK 2.x Graph Runtime)

MATE `graph` agents compile to the ADK 2.x Workflow graph engine. The graph is
configured in the agent's `planner_config`: in the agent form, **Configure** under
**Planner** opens **Planner Configuration**, where the JSON goes.

Graph agents run on the ADK runtime only. With `AGENT_FRAMEWORK=langgraph`, building
one fails with "has type 'graph' which is not supported by the langgraph runtime".

## Conditional routing

```json
{
  "edges": [
    ["START", "classifier"],
    {"from": "classifier", "to": "intent_router"},
    {"from": "intent_router", "to": "refund_agent", "route": "refund"},
    {"from": "intent_router", "to": ["faq_agent", "log_agent"], "route": "faq"}
  ],
  "router_nodes": [
    {
      "name": "intent_router",
      "state_key": "classifier_output",
      "routes": ["refund", "faq"],
      "default_route": "faq"
    }
  ]
}
```

- **`edges`** — list entries are sequential chains (`["a", "b", "c"]`); dict
  entries are `{"from", "to", "route"}`. `to` may be a list (fan-out), and so may
  `route` (any of them). A node is named by a sub-agent's name, a router's or join's
  name, or `START`.
  - An edge **without** `route` is always followed, whatever route the node emitted.
  - An edge with a `route` is followed when the node emits that route.
  - An edge with `"route": "__DEFAULT__"` is followed when none of the node's other
    routed edges matched.
  - Without `edges`, the sub-agents run one after another, in order.
- **`router_nodes`** — deterministic routers. Each reads `state[state_key]`
  (falling back to `state["<state_key>_output"]`, where LLM sub-agent output
  lands via `output_key`), matches it against `routes` (exact then substring,
  case-insensitive) and emits that route. No match → `default_route`; with no
  `default_route` either, it emits none, and only the router's edges without a
  `route` are followed.
- **`join_nodes`** — names treated as fan-in JoinNodes (names containing
  "join" are auto-detected).

The visual builder renders routing edges (dashed teal, route labels) and
router/join pills for graph agents with `planner_config.edges`.

## Retry (framework-level resilience)

```json
{
  "retry_config": {"max_attempts": 3, "initial_delay": 1.0, "backoff_factor": 2.0},
  "node_retry": {"refund_agent": {"max_attempts": 2}}
}
```

- `retry_config` in any agent's `planner_config` applies to that agent node
  (llm or graph).
- `node_retry` (graph agents) applies per sub-agent/router node.
- Note: broad `try/except` inside tools masks failures and disables framework
  retries — let exceptions propagate from tools that should be retried.

## Human-in-the-loop approval

- `tool_config: {"require_confirmation": ["tool_name", ...]}` wraps the named
  tools in `FunctionTool(require_confirmation=True)`: the invocation pauses
  and waits for user approval before executing the tool.
- `RESUMABILITY_ENABLED=true` (env, ADK runtime) makes the App resumable so paused/failed
  invocations can be resumed from the last event.

## App-wide plugin mode

On the ADK runtime, `MATE_PLUGINS_ENABLED=true` (env) registers
`shared/callbacks/mate_plugin.py` (RBAC, guardrails, user profile, token tracking,
model error logging) as an ADK Plugin on the App
instead of per-agent model callbacks. This covers every agent in the app,
including agents created at runtime via `create_agent_tool`. When enabled,
`agent_manager` skips per-agent callback wiring to avoid double execution.
