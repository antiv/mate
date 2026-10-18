"""
Session replay: turn the spans stored in trace_spans into a timeline per turn.

One chat turn is one trace. For each turn this says when each agent ran
(ADK's invoke_agent spans), which model calls it made and on which model
(MATE's gen_ai.inference spans, marked when the fallback model answered), and
which tools it called (ADK's execute_tool spans, or MATE's mate.tool spans when
ADK's are missing). build_timeline() puts a session's turns back to back on one
clock, which is what the Visual Builder plays back on its canvas.

Times are milliseconds from the start of the turn. Spans only exist when
tracing and its database export are on, and only on the ADK runtime.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

AGENT_ATTRS = ("gen_ai.agent.name", "mate.agent.name")
CONVERSATION_ATTR = "gen_ai.conversation.id"


def to_datetime(value: Any) -> Optional[datetime]:
    """A span time as a timezone-aware datetime; SQLite returns text, the others datetimes."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _span_kind(span: Dict[str, Any]) -> Optional[str]:
    attrs = span["attributes"]
    operation = attrs.get("gen_ai.operation.name")
    name = span["name"] or ""
    if operation == "invoke_agent":
        return "agent"
    if operation == "execute_tool":
        return "tool"
    if name == "mate.tool":
        return "mate_tool"
    if name == "gen_ai.inference":
        return "llm"
    if name.startswith("call_llm") or operation in ("generate_content", "chat"):
        return "adk_llm"
    return None


def _agent_of(span: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """The span's own agent attribute, else the nearest ancestor's."""
    seen = set()
    current: Optional[Dict[str, Any]] = span
    while current is not None and current["span_id"] not in seen:
        seen.add(current["span_id"])
        for key in AGENT_ATTRS:
            if current["attributes"].get(key):
                return str(current["attributes"][key])
        current = by_id.get(current.get("parent_span_id") or "")
    return None


def build_turn(trace_id: str, spans: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The timeline of one trace, or None when it has no span the replay can show.

    Each span is a dict with span_id, parent_span_id, name, start_time, end_time,
    attributes (a dict), status and error_message.
    """
    timed = [s for s in spans if to_datetime(s["start_time"]) is not None]
    if not timed:
        return None
    by_id = {s["span_id"]: s for s in timed}
    start = min(to_datetime(s["start_time"]) for s in timed)
    end = max(to_datetime(s["end_time"]) or to_datetime(s["start_time"]) for s in timed)

    kinds = {s["span_id"]: _span_kind(s) for s in timed}
    present = set(kinds.values())
    # ADK and MATE both trace model calls and tool calls. Keep one of each pair:
    # MATE's inference span (it knows about the fallback), ADK's tool span.
    skip = {"adk_llm"} if "llm" in present else set()
    if "tool" in present:
        skip.add("mate_tool")

    events = []
    for s in timed:
        kind = kinds[s["span_id"]]
        if kind is None or kind in skip:
            continue
        attrs = s["attributes"]
        st = to_datetime(s["start_time"])
        et = to_datetime(s["end_time"]) or st
        kind = {"mate_tool": "tool", "adk_llm": "llm"}.get(kind, kind)
        event = {
            "kind": kind,
            "agent": _agent_of(s, by_id),
            "start_ms": round((st - start).total_seconds() * 1000),
            "end_ms": round((et - start).total_seconds() * 1000),
            "status": s.get("status") or "UNSET",
            "error": s.get("error_message"),
        }
        if kind == "llm":
            fallback = bool(attrs.get("mate.model.fallback"))
            event["model"] = attrs.get("gen_ai.response.model") or attrs.get("gen_ai.request.model")
            event["fallback"] = fallback
            if fallback:
                event["primary_model"] = attrs.get("gen_ai.request.model")
        elif kind == "tool":
            event["tool"] = attrs.get("gen_ai.tool.name") or attrs.get("mate.tool.name") or s["name"]
        if event["agent"] is None:
            continue
        events.append(event)

    if not events:
        return None
    events.sort(key=lambda e: (e["start_ms"], e["kind"] != "agent", e["end_ms"]))

    return {
        "trace_id": trace_id,
        "started_at": start.isoformat(),
        "duration_ms": round((end - start).total_seconds() * 1000),
        "events": events,
        "agents": summarize_agents(events),
    }


def summarize_agents(events: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Per agent: when it first ran, time spent, model and tool calls, fallback, errors."""
    agents: Dict[str, Dict[str, Any]] = {}
    for e in events:
        a = agents.setdefault(e["agent"], {
            "first_ms": e["start_ms"], "busy_ms": 0, "llm_calls": 0, "tool_calls": 0,
            "fallback": False, "error": False, "models": [],
        })
        a["first_ms"] = min(a["first_ms"], e["start_ms"])
        if e["kind"] == "agent":
            a["busy_ms"] += e["end_ms"] - e["start_ms"]
        elif e["kind"] == "llm":
            a["llm_calls"] += 1
            a["fallback"] = a["fallback"] or e["fallback"]
            if e.get("model") and e["model"] not in a["models"]:
                a["models"].append(e["model"])
        elif e["kind"] == "tool":
            a["tool_calls"] += 1
        if e["status"] == "ERROR":
            a["error"] = True
    return agents


# The time between two turns is mostly the person typing; the session timeline
# replaces it with this short pause so the turns play back as one run.
TURN_GAP_MS = 800


def build_timeline(turns: List[Dict[str, Any]], gap_ms: int = TURN_GAP_MS) -> Dict[str, Any]:
    """All turns of a session back to back, on one clock, each event tagged with its turn."""
    events: List[Dict[str, Any]] = []
    markers = []
    offset = 0
    for index, turn in enumerate(turns):
        if index:
            offset += gap_ms
        markers.append({"index": index, "trace_id": turn["trace_id"], "started_at": turn["started_at"],
                        "start_ms": offset, "end_ms": offset + turn["duration_ms"]})
        for e in turn["events"]:
            events.append(dict(e, turn=index, start_ms=e["start_ms"] + offset, end_ms=e["end_ms"] + offset))
        offset += turn["duration_ms"]
    return {"duration_ms": offset, "events": events, "agents": summarize_agents(events), "turns": markers}


def build_replay(session_id: str, spans: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Every turn of a session, oldest first, from the spans of its traces."""
    by_trace: Dict[str, List[Dict[str, Any]]] = {}
    for s in spans:
        by_trace.setdefault(s["trace_id"], []).append(s)
    turns = [t for t in (build_turn(tid, ss) for tid, ss in by_trace.items()) if t]
    turns.sort(key=lambda t: t["started_at"])
    return {"session_id": session_id, "turns": turns, "timeline": build_timeline(turns)}


def session_of(span: Dict[str, Any]) -> Optional[str]:
    """The session (conversation) id a span was recorded for, if it says."""
    value = span["attributes"].get(CONVERSATION_ATTR)
    return str(value) if value else None
