**Title:** I stopped letting the LLM decide my agent flow — deterministic routing, per-node retries and human approval gates, all from JSON config (ADK 2.0)

---

Google's pitch for ADK 2.0 boils down to one production lesson: agents that *reason* about where to go next get stuck in loops, hallucinate past your business logic, and fail without clean exceptions. The fix is a workflow graph where **routing is code and only the reasoning is LLM**. MATE already ran on the ADK 2.x graph engine under the hood — but only to emulate the old sequential/parallel/loop patterns. This update exposes the actually interesting parts, and everything is declarative DB config you edit in the dashboard. No custom backend code, no redeploy.

## Conditional routing without trusting the model

A graph agent's `planner_config` now takes route-conditional edges plus **router nodes**:

```json
{
  "edges": [
    ["START", "intent_classifier"],
    {"from": "intent_classifier", "to": "intent_router"},
    {"from": "intent_router", "to": "refund_agent", "route": "refund"},
    {"from": "intent_router", "to": "faq_agent", "route": "faq"}
  ],
  "router_nodes": [
    {"name": "intent_router", "state_key": "intent_classifier_output",
     "routes": ["refund", "faq"], "default_route": "faq"}
  ]
}
```

The classifier LLM writes one word into session state; the router is a plain function that matches it against the allowed routes and picks the edge. The model **cannot** invent a branch that doesn't exist — a prompt injection can at worst pick the wrong *predefined* road, never a new one. Only the branch that matched executes (Google's benchmark for this pattern: ~50% fewer tokens, ~20% lower latency vs. letting an orchestrator agent decide). The visual builder renders the routing too — router pills and dashed edges labeled with their route.

## Retries where agents actually fail

`{"retry_config": {"max_attempts": 3, "backoff_factor": 2.0}}` on any agent, or `node_retry` per node inside a graph — framework-level, with exponential backoff. The gotcha that bit me: a broad `try/except` inside a tool swallows the exception before the framework sees it, so retries silently never happen. Let your tools throw.

## Human-in-the-loop for the scary tools

`tool_config: {"require_confirmation": ["place_order"]}` wraps the tool so the invocation **pauses** and waits for the user to approve before it executes. Combined with `RESUMABILITY_ENABLED=true`, a paused (or crashed) invocation can resume from its last event instead of restarting the conversation.

## One plugin instead of N callback wires

RBAC, guardrails and token tracking used to be attached to every agent individually. Behind a flag they now run as a single app-wide **ADK Plugin** — which also covers agents that other agents create at runtime, the exact spot where per-agent wiring used to leak.

## The takeaway

The interesting shift isn't any single feature — it's that "which agent runs next" moved from the prompt to the graph. The LLM does language; the graph does control flow. Happy to share the router-node implementation or the confirmation flow if anyone's wiring up something similar on ADK 2.x.
