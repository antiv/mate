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

Every path that deletes agents calls delete_agent_rows() in the same transaction:
the dashboard's agent and project delete, the create_agent tool and the wizard's
trial cleanup. Logs (token usage, guardrails, responses, feedback, audit) stay:
they record what happened and are not read by the agent.
"""

import logging
from typing import Dict, Iterable, List

from sqlalchemy import inspect, text

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
