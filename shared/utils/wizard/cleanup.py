"""TTL cleanup for expired wizard trial agents.

Trials are throwaway: a project named ``trial_<tier>_<token>`` plus its agent(s) and a
widget key. Once a trial's ``expires_at`` passes (or an orphaned ``trial_*`` project is older
than the TTL), everything is removed. Leads are kept — they denormalize the trial reference.
"""

import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared.utils.database_client import get_database_client
from shared.utils.models import (
    AgentConfig, AgentConfigVersion, Credential, MemoryBlock, Project, WidgetApiKey, WizardSession,
)

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _delete_agent_folder(agent_name: str) -> None:
    folder = _REPO_ROOT / "agents" / agent_name
    try:
        if folder.is_dir():
            shutil.rmtree(folder)
    except Exception as exc:
        logger.warning("Could not remove trial agent folder %s: %s", folder, exc)


def cleanup_expired_trials(ttl_days: int = None) -> dict:
    """Delete expired trial projects/agents/keys/credentials. Returns a small summary."""
    ttl_days = ttl_days if ttl_days is not None else int(os.getenv("WIZARD_TRIAL_TTL_DAYS", "7"))
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=ttl_days)

    db = get_database_client()
    session = db.get_session()
    removed_projects = 0
    try:
        # Projects referenced by an expired wizard session.
        expired_sessions = session.query(WizardSession).filter(
            WizardSession.trial_project_id.isnot(None),
            WizardSession.expires_at.isnot(None),
            WizardSession.expires_at < now,
        ).all()
        project_ids = {s.trial_project_id for s in expired_sessions}

        # Safety net: orphaned trial projects older than the TTL (no live session row).
        orphan_projects = session.query(Project).filter(
            Project.name.like("trial\\_%", escape="\\"),
            Project.created_at < cutoff,
        ).all()
        project_ids.update(p.id for p in orphan_projects)

        if not project_ids:
            return {"removed_projects": 0}

        all_agent_names = []
        for pid in project_ids:
            all_agent_names.extend(_delete_trial_project(session, pid))
            removed_projects += 1

        session.commit()
        for name in all_agent_names:
            _delete_agent_folder(name)
    except Exception as exc:
        session.rollback()
        logger.exception("Wizard trial cleanup failed: %s", exc)
        return {"error": str(exc), "removed_projects": removed_projects}
    finally:
        session.close()

    if removed_projects:
        logger.info("Wizard cleanup removed %d expired trial project(s)", removed_projects)
        _reload_adk_agents()
    return {"removed_projects": removed_projects}


def _delete_trial_project(session, pid: int) -> list:
    """Delete a single trial project's agents/keys/blocks/credentials within an open session.

    Detaches wizard sessions (keeps denormalized leads). Returns the agent names so the
    caller can remove their folders after commit. Does not commit.
    """
    agents = session.query(AgentConfig).filter(AgentConfig.project_id == pid).all()
    agent_names = [a.name for a in agents]
    agent_ids = [a.id for a in agents]

    session.query(WidgetApiKey).filter(WidgetApiKey.project_id == pid).delete(synchronize_session=False)
    session.query(MemoryBlock).filter(MemoryBlock.project_id == pid).delete(synchronize_session=False)
    if agent_names:
        session.query(Credential).filter(Credential.app_name.in_(agent_names)).delete(synchronize_session=False)
    # Delete version snapshots then agents explicitly (avoids the ORM nulling the
    # NOT NULL agent_config_versions.agent_config_id on project cascade).
    if agent_ids:
        session.query(AgentConfigVersion).filter(
            AgentConfigVersion.agent_config_id.in_(agent_ids)
        ).delete(synchronize_session=False)
        session.query(AgentConfig).filter(AgentConfig.id.in_(agent_ids)).delete(synchronize_session=False)

    session.query(WizardSession).filter(WizardSession.trial_project_id == pid).update(
        {"trial_project_id": None, "status": "expired"}, synchronize_session=False
    )
    session.query(Project).filter(Project.id == pid).delete(synchronize_session=False)
    return agent_names


def release_trial(project_id: int) -> dict:
    """Immediately delete one trial project (e.g. when a lead is archived). Leads survive."""
    db = get_database_client()
    session = db.get_session()
    try:
        if not session.query(Project).filter(Project.id == project_id).first():
            return {"released": False, "reason": "not_found"}
        agent_names = _delete_trial_project(session, project_id)
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.exception("release_trial failed for project %s: %s", project_id, exc)
        return {"error": str(exc)}
    finally:
        session.close()

    for name in agent_names:
        _delete_agent_folder(name)
    _reload_adk_agents()
    return {"released": True}


def _reload_adk_agents() -> None:
    try:
        import httpx
        from shared.utils.utils import get_adk_config
        cfg = get_adk_config()
        url = f"http://{cfg['adk_host']}:{cfg['adk_port']}/api/reload-all-agents"
        with httpx.Client(timeout=30.0) as client:
            client.post(url)
    except Exception:
        pass
