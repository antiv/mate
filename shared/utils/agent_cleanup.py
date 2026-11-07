"""
What deleting an agent removes, besides its agents_config row.

Many tables refer to an agent by name, not by id, with no foreign key to cascade
along. Left behind, they are inherited by the next agent created with the same
name, which may belong to another project and another admin:

- links and settings: its file search store links (which may point at another
  project's stores), widget keys, chat integrations and triggers, which would
  expose or run the new agent; its budgets, alert rules and eval test cases;
- what its users left with it: stored OAuth credentials (Google Drive, Calendar),
  conversations on both runtimes, the memory built from them, and app-wide state.

A rename is the same problem: the old name is free afterwards. rename_agent_rows()
moves every row delete_agent_rows() would remove, and the agent's ratings, to the new
name, so the renamed agent keeps its links, settings and conversations and the old
name brings nothing to the next agent that takes it. The dashboard's update and
rollback call it when the name changes.

Every path that deletes agents calls delete_agent_rows() in the same transaction:
the dashboard's agent and project delete, the create_agent tool and the wizard's
trial cleanup. Logs (token usage, guardrails, responses, feedback, audit) stay:
they record what happened and are not read by the agent.
"""

import logging
from typing import Dict, Iterable, List

from sqlalchemy import bindparam, inspect, text

logger = logging.getLogger(__name__)

# ADK's DatabaseSessionService keeps conversations in the same database, keyed by
# app_name (the root agent): google/adk/sessions/schemas. Events first, they
# reference sessions.
ADK_SESSION_TABLES = ("events", "sessions", "app_states", "user_states")


def delete_agent_rows(session, agent_names: Iterable[str]) -> Dict[str, object]:
    """Delete the rows keyed by these agents' names, in the caller's transaction.

    Returns the number of rows removed per table, and under "langgraph_threads"
    the LangGraph session ids whose checkpoints purge_langgraph_checkpoints()
    should remove once the transaction has committed. Does not commit.
    """
    from .models import (AgentFileSearchStore, AgentTrigger, AlertRule, ChannelIntegration,
                         Credential, EvalResult, LangGraphEvent, LangGraphSession, MemoryEvent,
                         MemorySession, RateLimitConfig, TestCase, WidgetApiKey)

    names: List[str] = sorted({n for n in agent_names if n})
    removed: Dict[str, object] = {}
    if not names:
        removed["langgraph_threads"] = []
        return removed

    def delete(label, query):
        count = query.delete(synchronize_session=False)
        if count:
            removed[label] = count

    # Links and settings
    delete("agent_file_search_stores",
           session.query(AgentFileSearchStore).filter(AgentFileSearchStore.agent_name.in_(names)))
    delete("widget_api_keys", session.query(WidgetApiKey).filter(WidgetApiKey.agent_name.in_(names)))
    delete("channel_integrations",
           session.query(ChannelIntegration).filter(ChannelIntegration.agent_name.in_(names)))
    delete("agent_triggers", session.query(AgentTrigger).filter(AgentTrigger.agent_name.in_(names)))
    delete("rate_limit_config", session.query(RateLimitConfig).filter(
        RateLimitConfig.scope == "agent", RateLimitConfig.scope_id.in_(names)))
    delete("alert_rules", session.query(AlertRule).filter(
        AlertRule.scope == "agent", AlertRule.scope_id.in_(names)))
    test_case_ids = [row.id for row in session.query(TestCase.id).filter(TestCase.agent_name.in_(names))]
    if test_case_ids:
        delete("eval_results", session.query(EvalResult).filter(EvalResult.test_case_id.in_(test_case_ids)))
        delete("test_cases", session.query(TestCase).filter(TestCase.id.in_(test_case_ids)))

    # What its users left with it
    delete("credentials", session.query(Credential).filter(Credential.app_name.in_(names)))
    memory_ids = [row.session_id for row in
                  session.query(MemorySession.session_id).filter(MemorySession.app_name.in_(names))]
    if memory_ids:
        delete("memory_events", session.query(MemoryEvent).filter(MemoryEvent.session_id.in_(memory_ids)))
        delete("memory_sessions", session.query(MemorySession).filter(MemorySession.session_id.in_(memory_ids)))
    threads = [row.id for row in session.query(LangGraphSession.id).filter(LangGraphSession.app_name.in_(names))]
    if threads:
        delete("lg_events", session.query(LangGraphEvent).filter(LangGraphEvent.session_id.in_(threads)))
        delete("lg_sessions", session.query(LangGraphSession).filter(LangGraphSession.id.in_(threads)))
    removed["langgraph_threads"] = threads

    inspector = inspect(session.connection())
    existing = set(inspector.get_table_names())
    for table in ADK_SESSION_TABLES:
        if table in existing and "app_name" in {c["name"] for c in inspector.get_columns(table)}:
            # Table names are constants; the agent names are bound parameters
            count = 0
            for name in names:
                count += session.execute(text(f"DELETE FROM {table} WHERE app_name = :name"),
                                         {"name": name}).rowcount or 0
            if count:
                removed[f"adk_{table}"] = count
    return removed


def purge_langgraph_checkpoints(thread_ids: Iterable[str]) -> None:
    """Delete the LangGraph checkpoints of these sessions. Never raises.

    The checkpoints live in their own SQLite file (see agent_builder.get_checkpointer),
    outside the transaction, so this runs after the rows are deleted.
    """
    thread_ids = [t for t in thread_ids if t]
    if not thread_ids:
        return
    from .langgraph.agent_builder import CHECKPOINT_DB_PATH
    if not CHECKPOINT_DB_PATH.exists():
        return
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        with SqliteSaver.from_conn_string(str(CHECKPOINT_DB_PATH)) as saver:
            for thread_id in thread_ids:
                saver.delete_thread(thread_id)
    except Exception as e:
        logger.error(f"Could not delete the LangGraph checkpoints of {len(thread_ids)} session(s): {e}")


def rename_agent_rows(session, old_name: str, new_name: str) -> Dict[str, object]:
    """Move the rows keyed by an agent's old name to its new one, in the caller's
    transaction. Rows already keyed by the new name, left by an agent that had it
    before, are deleted first: the renamed agent does not inherit them. Returns the
    number of rows moved per table and, under "langgraph_threads", the threads
    delete_agent_rows() removed for the new name. Does not commit.

    ADK's events reference their session by (app_name, user_id, id), with no ON UPDATE,
    so its sessions are copied under the new name, their events moved, and the old
    sessions deleted."""
    from .models import (AgentFileSearchStore, AgentTrigger, AlertRule, ChannelIntegration,
                         Credential, LangGraphSession, MemorySession, RateLimitConfig,
                         ResponseFeedback, TestCase, WidgetApiKey)

    if not old_name or not new_name or old_name == new_name:
        return {"langgraph_threads": []}
    cleared = delete_agent_rows(session, [new_name])
    session.query(ResponseFeedback).filter(ResponseFeedback.agent_name == new_name).delete(
        synchronize_session=False)
    moved: Dict[str, object] = {"langgraph_threads": cleared["langgraph_threads"]}

    def move(label, query, column):
        count = query.update({column: new_name}, synchronize_session=False)
        if count:
            moved[label] = count

    for label, model, column in (
            ("agent_file_search_stores", AgentFileSearchStore, AgentFileSearchStore.agent_name),
            ("widget_api_keys", WidgetApiKey, WidgetApiKey.agent_name),
            ("channel_integrations", ChannelIntegration, ChannelIntegration.agent_name),
            ("agent_triggers", AgentTrigger, AgentTrigger.agent_name),
            ("test_cases", TestCase, TestCase.agent_name),
            ("response_feedback", ResponseFeedback, ResponseFeedback.agent_name),
            ("credentials", Credential, Credential.app_name),
            ("memory_sessions", MemorySession, MemorySession.app_name),
            ("lg_sessions", LangGraphSession, LangGraphSession.app_name)):
        move(label, session.query(model).filter(column == old_name), column)
    move("rate_limit_config", session.query(RateLimitConfig).filter(
        RateLimitConfig.scope == "agent", RateLimitConfig.scope_id == old_name), RateLimitConfig.scope_id)
    move("alert_rules", session.query(AlertRule).filter(
        AlertRule.scope == "agent", AlertRule.scope_id == old_name), AlertRule.scope_id)

    inspector = inspect(session.connection())
    existing = set(inspector.get_table_names())
    params = {"old": old_name, "new": new_name}
    if {"sessions", "events"} <= existing:
        # Column names come from the schema, the agent names are bound parameters
        columns = [c["name"] for c in inspector.get_columns("sessions") if c["name"] != "app_name"]
        listed = ", ".join(columns)
        copied = session.execute(text(
            f"INSERT INTO sessions (app_name, {listed}) "
            f"SELECT :new, {listed} FROM sessions WHERE app_name = :old"), params).rowcount or 0
        session.execute(text("UPDATE events SET app_name = :new WHERE app_name = :old"), params)
        session.execute(text("DELETE FROM sessions WHERE app_name = :old"), params)
        if copied:
            moved["adk_sessions"] = copied
    for table in ("app_states", "user_states"):
        if table in existing:
            count = session.execute(text(f"UPDATE {table} SET app_name = :new WHERE app_name = :old"),
                                    params).rowcount or 0
            if count:
                moved[f"adk_{table}"] = count
    return moved


def left_behind(session, name: str) -> bool:
    """True if rows are keyed by this name with no agent of that name to own them: left
    by an agent renamed under 1.x, before rename_agent_rows. They are the default
    workspace's, so another workspace's new agent may not take the name and inherit
    them. Ratings don't count, the eval pages keep them to their project; nor does a
    trace that records its workspace (mate.workspace.id), which the trace pages keep
    to it. Spans from before V046 record none."""
    from .models import (AgentFileSearchStore, AgentTrigger, AlertRule, ChannelIntegration,
                         Credential, LangGraphSession, MemorySession, RateLimitConfig,
                         TestCase, WidgetApiKey)

    for column in (AgentFileSearchStore.agent_name, WidgetApiKey.agent_name,
                   ChannelIntegration.agent_name, AgentTrigger.agent_name, TestCase.agent_name,
                   Credential.app_name, MemorySession.app_name, LangGraphSession.app_name):
        if session.query(column).filter(column == name).first():
            return True
    for model in (RateLimitConfig, AlertRule):
        if session.query(model.id).filter(model.scope == "agent", model.scope_id == name).first():
            return True
    existing = set(inspect(session.connection()).get_table_names())
    for table in ADK_SESSION_TABLES:
        if table in existing and session.execute(
                text(f"SELECT 1 FROM {table} WHERE app_name = :name"), {"name": name}).first():
            return True
    if "trace_spans" in existing:
        # The name as a JSON string value. A name is an identifier, so only _ is a LIKE
        # wildcard in it: a near name may also count, never fewer
        dialect = session.get_bind().dialect.name
        attributes = {"postgresql": "CAST(attributes AS TEXT)",
                      "mysql": "CAST(attributes AS CHAR)"}.get(dialect, "attributes")
        traces = {row[0] for row in session.execute(text(
            f"SELECT DISTINCT trace_id FROM trace_spans WHERE {attributes} LIKE :pattern"),
            {"pattern": f'%"{name}"%'})}
        if traces:
            recorded = {row[0] for row in session.execute(text(
                f"SELECT DISTINCT trace_id FROM trace_spans WHERE trace_id IN :traces "
                f"AND {attributes} LIKE '%mate.workspace.id%'").bindparams(
                    bindparam("traces", expanding=True)), {"traces": sorted(traces)})}
            if traces - recorded:
                return True
    return False
