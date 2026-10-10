"""
Playground: one agent, two or three variants, the same prompts, side by side.

A variant is the agent's config with another model, other instructions, or a
stored version in their place. Each runs in memory through SnapshotAgent, as
evals do: nothing is saved or deployed. Every reply is timed, scored when it
has an expected output (the agent's eval suite, or an expected answer typed
with a prompt), and priced from the token_usage_logs rows its run wrote, which
both runtimes key by the run's invocation id.

The runs are real model calls: they appear on the Usage page under the agent,
by the eval user, like eval runs do.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

logger = logging.getLogger(__name__)

MAX_VARIANTS = 3
MIN_VARIANTS = 2


@dataclass
class Case:
    """A prompt to run; with an expected output, the reply is scored like an eval test case."""
    input: str
    expected_output: Optional[str] = None
    eval_method: str = "exact_match"
    threshold: float = 0.7
    judge_model: Optional[str] = None
    id: Optional[int] = None


@dataclass
class Variant:
    label: str
    snapshot: Dict[str, Any]
    results: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None


def _invocation_ids(events: Iterable[Dict[str, Any]]) -> List[str]:
    """The run ids in a turn's events: ADK dumps them snake_case, LangGraph camelCase."""
    ids: List[str] = []
    for event in events:
        value = event.get("invocation_id") or event.get("invocationId")
        if value and value not in ids:
            ids.append(value)
    return ids


async def run_variant(variant: Variant, cases: List[Case], scorer: Any,
                      agent_factory: Optional[Any] = None) -> Variant:
    """Ask the variant every case, in order, each in a fresh session."""
    if agent_factory is None:
        from .eval_agent_runner import SnapshotAgent as agent_factory
    try:
        async with agent_factory(variant.snapshot) as agent:
            for case in cases:
                events: List[Dict[str, Any]] = []
                started = time.monotonic()
                try:
                    output = await agent.ask(case.input, events=events)
                except Exception as e:
                    variant.results.append({"output": None, "error": f"{type(e).__name__}: {e}",
                                            "latency_ms": int((time.monotonic() - started) * 1000),
                                            "score": None, "passed": None,
                                            "invocation_ids": _invocation_ids(events)})
                    continue
                result = {"output": output, "error": None,
                          "latency_ms": int((time.monotonic() - started) * 1000),
                          "score": None, "passed": None,
                          "invocation_ids": _invocation_ids(events)}
                if case.expected_output:
                    scored = scorer.score_output(case, output, None)
                    result.update(score=scored.score, passed=scored.passed)
                    if scored.error:
                        result["error"] = scored.error
                variant.results.append(result)
    except Exception as e:
        # The agent could not be built: an unknown model, a broken tool
        variant.error = f"{type(e).__name__}: {e}"
    return variant


def add_costs(session, variants: List[Variant]) -> None:
    """Price each reply from the token rows its run wrote, and sum each variant."""
    from sqlalchemy import case, func
    from .models import TokenUsageLog

    ids = [i for v in variants for r in v.results for i in r["invocation_ids"]]
    usage: Dict[str, Dict[str, Any]] = {}
    if ids:
        rows = session.query(
            TokenUsageLog.request_id,
            func.sum(TokenUsageLog.cost_usd),
            func.sum(case((TokenUsageLog.cost_usd.is_(None), 1), else_=0)),
            func.sum(func.coalesce(TokenUsageLog.prompt_tokens, 0) + func.coalesce(TokenUsageLog.response_tokens, 0)),
        ).filter(TokenUsageLog.request_id.in_(ids), TokenUsageLog.status == "SUCCESS") \
            .group_by(TokenUsageLog.request_id).all()
        usage = {rid: {"cost": cost, "unpriced": int(unpriced or 0), "tokens": int(tokens or 0)}
                 for rid, cost, unpriced, tokens in rows}

    for variant in variants:
        for result in variant.results:
            parts = [usage[i] for i in result["invocation_ids"] if i in usage]
            result["tokens"] = sum(p["tokens"] for p in parts)
            result["unpriced_calls"] = sum(p["unpriced"] for p in parts)
            priced = [p["cost"] for p in parts if p["cost"] is not None]
            result["cost_usd"] = float(sum(priced)) if priced else None


def summarize(variant: Variant) -> Dict[str, Any]:
    results = variant.results
    scored = [r["score"] for r in results if r.get("score") is not None]
    latencies = [r["latency_ms"] for r in results if r.get("output") is not None]
    costs = [r["cost_usd"] for r in results if r.get("cost_usd") is not None]
    return {
        "runs": len(results),
        "errors": sum(1 for r in results if r.get("output") is None),
        "scored": len(scored),
        "passed": sum(1 for r in results if r.get("passed")),
        "avg_score": round(sum(scored) / len(scored), 3) if scored else None,
        "avg_latency_ms": int(sum(latencies) / len(latencies)) if latencies else None,
        "cost_usd": float(sum(costs)) if costs else None,
        "tokens": sum(r.get("tokens", 0) for r in results),
        "unpriced_calls": sum(r.get("unpriced_calls", 0) for r in results),
    }
