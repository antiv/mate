"""
The project and workspace a log row belongs to: token_usage_logs, guardrail_logs,
agent_responses and audit_logs carry project_id (migration V044) and workspace_id
(V046).

Both are copied onto a row when the row is written, so a cost or an audit entry stays
with the project it happened in when its agent is later renamed, moved or deleted
(documents/design/workspaces.md, D2). Rows written before V044 are filled in from
their agent's current project by backfill(), and rows written before V046 from their
project's workspace by backfill_workspaces(). The auth server runs both once in the
background, in batches so a large table is never locked for long.
"""

import logging
import threading
from typing import Callable, Dict, List, Optional

from sqlalchemy import String, Update, cast, func, select, update
from sqlalchemy.orm import Session

from .database_client import get_database_client
from .models import AgentConfig, AgentResponse, AuditLog, GuardrailLog, Project, TokenUsageLog

logger = logging.getLogger(__name__)

BATCH_SIZE = 10_000
# system_settings key marking the backfill as done, so later starts skip it
BACKFILL_DONE_KEY = "log_project_backfill"
WORKSPACE_BACKFILL_DONE_KEY = "log_workspace_backfill"

# Audit entries name their resource in resource_id: an agent by name, a project by id
RESOURCE_AGENT = "agent"
RESOURCE_PROJECT = "project"


def project_of_agent(session, agent_name: Optional[str]) -> Optional[int]:
    """The agent's project, or None for no agent or an unknown one."""
    if not agent_name:
        return None
    return session.query(AgentConfig.project_id).filter(AgentConfig.name == agent_name).scalar()


def scope_of_agent(session: Session, agent_name: Optional[str]) -> Dict[str, Optional[int]]:
    """project_id and workspace_id for a log row about this agent; None for an unknown one."""
    row = None
    if agent_name:
        row = (session.query(AgentConfig.project_id, Project.workspace_id)
               .outerjoin(Project, Project.id == AgentConfig.project_id)
               .filter(AgentConfig.name == agent_name).first())
    return {"project_id": row[0] if row else None, "workspace_id": row[1] if row else None}


def project_of_audit_entry(session, resource_type: str, resource_id: Optional[str]) -> Optional[int]:
    """The project an audit entry is about, or None for an installation-wide one."""
    if not resource_id:
        return None
    if resource_type == RESOURCE_AGENT:
        return project_of_agent(session, resource_id)
    if resource_type == RESOURCE_PROJECT and str(resource_id).isdigit():
        # Kept when the entry records the project's deletion
        return int(resource_id)
    return None


def _agent_project(model):
    return (select(AgentConfig.project_id)
            .where(AgentConfig.name == model.agent_name)
            .scalar_subquery())


def _backfill_statements(model, lo: int, hi: int):
    """UPDATE statements that fill project_id for rows lo <= id < hi."""
    in_batch = (model.id >= lo, model.id < hi, model.project_id.is_(None))
    if model is AuditLog:
        agent_project = (select(AgentConfig.project_id)
                         .where(AgentConfig.name == AuditLog.resource_id)
                         .scalar_subquery())
        project = (select(Project.id)
                   .where(cast(Project.id, String) == AuditLog.resource_id)
                   .scalar_subquery())
        return [
            update(AuditLog).where(*in_batch, AuditLog.resource_type == RESOURCE_AGENT,
                                   AuditLog.resource_id.in_(select(AgentConfig.name)))
            .values(project_id=agent_project),
            update(AuditLog).where(*in_batch, AuditLog.resource_type == RESOURCE_PROJECT,
                                   AuditLog.resource_id.in_(select(cast(Project.id, String))))
            .values(project_id=project),
        ]
    return [update(model).where(*in_batch, model.agent_name.in_(select(AgentConfig.name)))
            .values(project_id=_agent_project(model))]


def backfill(batch_size: int = BATCH_SIZE) -> Dict[str, int]:
    """Fill project_id on rows written before V044, from each agent's current project.

    Walks each table by id in batches of batch_size, one commit per batch. Rows
    whose agent no longer exists keep NULL. Safe to run again, or twice at once:
    it only writes rows that have no project. Returns rows updated per table.
    """
    return _in_batches(_backfill_statements, batch_size, "Log project backfill")


def _workspace_backfill_statements(model: type, lo: int, hi: int) -> List[Update]:
    """UPDATE statements that fill workspace_id from project_id for rows lo <= id < hi."""
    workspace = (select(Project.workspace_id)
                 .where(Project.id == model.project_id)
                 .scalar_subquery())
    return [update(model).where(model.id >= lo, model.id < hi, model.workspace_id.is_(None),
                                model.project_id.in_(select(Project.id)))
            .values(workspace_id=workspace)]


def backfill_workspaces(batch_size: int = BATCH_SIZE) -> Dict[str, int]:
    """Fill workspace_id on rows written before V046, from their project's workspace.

    Run after backfill(), whose project_id it reads. Rows without a project, or whose
    project is gone, keep NULL. Safe to run again. Returns rows updated per table.
    """
    return _in_batches(_workspace_backfill_statements, batch_size, "Log workspace backfill")


def _in_batches(statements_for: Callable[[type, int, int], List[Update]], batch_size: int,
                label: str) -> Dict[str, int]:
    """Run statements_for(model, lo, hi) over each log table by id, one commit per batch."""
    db = get_database_client()
    updated: Dict[str, int] = {}
    for model in (TokenUsageLog, GuardrailLog, AgentResponse, AuditLog):
        table = model.__tablename__
        updated[table] = 0
        session = db.get_session()
        try:
            low, high = session.query(func.min(model.id), func.max(model.id)).one()
        finally:
            session.close()
        if low is None:
            continue
        for lo in range(low, high + 1, batch_size):
            session = db.get_session()
            try:
                for statement in statements_for(model, lo, lo + batch_size):
                    updated[table] += session.execute(statement).rowcount or 0
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        logger.info("%s: %s rows of %s", label, updated[table], table)
    return updated


def run_backfill_once() -> None:
    """Run backfill(), then backfill_workspaces(), each unless an earlier run finished.

    The workspace backfill reads project_id, so it waits for the project one. Never raises.
    """
    from .system_settings import get_setting, set_setting
    for label, done_key, run in (("Log project backfill", BACKFILL_DONE_KEY, backfill),
                                 ("Log workspace backfill", WORKSPACE_BACKFILL_DONE_KEY,
                                  backfill_workspaces)):
        try:
            if get_setting(done_key):
                continue
            logger.info("%s: starting", label)
            updated = run()
            set_setting(done_key, "done", "system")
            logger.info("%s: done, %s", label, updated)
        except Exception as e:
            logger.error("%s failed, will retry on next start: %s", label, e)
            return


def start_backfill_thread() -> threading.Thread:
    """Run the backfill beside the server, so a large table does not delay startup."""
    thread = threading.Thread(target=run_backfill_once, name="log-project-backfill", daemon=True)
    thread.start()
    return thread
