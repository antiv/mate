"""
Workspaces, phase 1 of 2.0 (documents/design/2.0-plan.md): the schema, with no change
in behaviour.

Every 1.x row belongs to the default workspace (V046). Until memberships are edited on
their own (phase 3), a user's membership of it is derived from users.roles
(documents/design/workspaces.md, D3):
- the admin role makes the user an owner, anything else a member;
- agent_roles are the user's roles without admin.

sync_default_members() brings every membership in line when the auth server starts,
which also adds the users of an installation that has just migrated. Listeners on the
User model do the same for each user inserted, or whose roles change, after that, so
the memberships are right whenever phase 2 starts reading them. models.py imports this
module, so the listeners are registered wherever users are written.
"""

import json
import logging
from typing import Dict, List, Optional, Tuple

from sqlalchemy import event, insert, update
from sqlalchemy.orm import attributes

from .models import DEFAULT_WORKSPACE_ID, Project, User, WorkspaceMember

logger = logging.getLogger(__name__)

ROLE_OWNER = "owner"
ROLE_MEMBER = "member"


def membership_from_roles(roles: List[str]) -> Tuple[str, List[str]]:
    """(role, agent_roles) in the default workspace for a user with these roles."""
    role = ROLE_OWNER if "admin" in roles else ROLE_MEMBER
    return role, [r for r in roles if r != "admin"]


def _member_values(roles: List[str]) -> Dict[str, str]:
    role, agent_roles = membership_from_roles(roles)
    return {"role": role, "agent_roles": json.dumps(agent_roles)}


def _upsert_default_member(connection, user_id: str, roles: List[str]) -> None:
    members = WorkspaceMember.__table__
    values = _member_values(roles)
    result = connection.execute(
        update(members)
        .where(members.c.workspace_id == DEFAULT_WORKSPACE_ID, members.c.user_id == user_id)
        .values(**values))
    if not result.rowcount:
        connection.execute(insert(members).values(
            workspace_id=DEFAULT_WORKSPACE_ID, user_id=user_id, **values))


@event.listens_for(User, "after_insert")
def _member_for_new_user(mapper, connection, user: User) -> None:
    _upsert_default_member(connection, user.user_id, user.get_roles())


@event.listens_for(User, "after_update")
def _member_for_changed_roles(mapper, connection, user: User) -> None:
    if attributes.get_history(user, "roles").has_changes():
        _upsert_default_member(connection, user.user_id, user.get_roles())


def sync_default_members(session) -> Dict[str, int]:
    """Make every user's membership of the default workspace match users.roles.

    Returns how many memberships were added and updated. Safe to run on every start.
    """
    members = {m.user_id: m for m in session.query(WorkspaceMember)
               .filter(WorkspaceMember.workspace_id == DEFAULT_WORKSPACE_ID)}
    added = updated = 0
    for user in session.query(User):
        values = _member_values(user.get_roles())
        member = members.get(user.user_id)
        if member is None:
            session.add(WorkspaceMember(workspace_id=DEFAULT_WORKSPACE_ID, user_id=user.user_id,
                                        **values))
            added += 1
        elif (member.role, member.agent_roles) != (values["role"], values["agent_roles"]):
            member.role = values["role"]
            member.agent_roles = values["agent_roles"]
            updated += 1
    session.commit()
    return {"added": added, "updated": updated}


def run_member_sync(db_client) -> None:
    """sync_default_members() at startup. Never raises."""
    session = db_client.get_session() if db_client else None
    if not session:
        return
    try:
        counts = sync_default_members(session)
        if counts["added"] or counts["updated"]:
            logger.info("Default workspace members: %s added, %s updated",
                        counts["added"], counts["updated"])
    except Exception as e:
        session.rollback()
        logger.error("Could not sync the default workspace members: %s", e)
    finally:
        session.close()


def workspace_of_project(session, project_id: Optional[int]) -> Optional[int]:
    """The project's workspace, or None for no project or an unknown one."""
    if project_id is None:
        return None
    return session.query(Project.workspace_id).filter(Project.id == project_id).scalar()
