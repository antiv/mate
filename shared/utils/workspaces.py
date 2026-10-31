"""
Workspaces, phase 1 of 2.0 (documents/design/2.0-plan.md): the schema, with no change
in behaviour.

Every 1.x row belongs to the default workspace (V046). Until memberships are edited on
their own (phase 3), a user's membership of it is derived from users.roles
(documents/design/workspaces.md, D3):
- the admin role makes the user an owner, anything else a member;
- agent_roles are the user's roles without admin;
- a widget visitor or a pending sign-up is no member at all. A widget reaches its
  workspace through its key's project, and a pending user has not been let in yet;
  approving them replaces 'pending' and so adds the membership.

sync_default_members() brings every membership in line when the auth server starts,
which also adds the users of an installation that has just migrated. Listeners on the
User model do the same for each user inserted, or whose roles change, after that, so
the memberships are right whenever phase 2 starts reading them. Both write through
_write_members(). models.py imports this module, so the listeners are registered
wherever users are written.
"""

import json
import logging
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from sqlalchemy import delete, event, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper, Session, attributes

from .models import DEFAULT_WORKSPACE_ID, Project, User, WorkspaceMember, parse_roles

if TYPE_CHECKING:
    from .database_client import DatabaseClient

logger = logging.getLogger(__name__)

ROLE_OWNER = "owner"
ROLE_MEMBER = "member"
# Users with one of these roles belong to no workspace (see the module docstring)
NON_MEMBER_ROLES = {"widget", "pending"}

# (role, agent_roles as stored) of a membership; None for no membership
Membership = Optional[Tuple[str, str]]

_DELETE_CHUNK = 500


def membership_from_roles(roles: List[str]) -> Optional[Tuple[str, List[str]]]:
    """(role, agent_roles) in the default workspace for a user with these roles, or
    None when such a user is no member."""
    if NON_MEMBER_ROLES.intersection(roles):
        return None
    role = ROLE_OWNER if "admin" in roles else ROLE_MEMBER
    return role, [r for r in roles if r != "admin"]


def _membership(raw_roles: Optional[str]) -> Membership:
    membership = membership_from_roles(parse_roles(raw_roles))
    return (membership[0], json.dumps(membership[1])) if membership else None


def _write_members(connection: Connection,
                   changes: Dict[str, Tuple[Membership, Membership]]) -> Dict[str, int]:
    """Apply {user_id: (current, wanted)} to the default workspace; returns counts."""
    members = WorkspaceMember.__table__
    in_default = members.c.workspace_id == DEFAULT_WORKSPACE_ID
    added = [{"workspace_id": DEFAULT_WORKSPACE_ID, "user_id": user_id,
              "role": wanted[0], "agent_roles": wanted[1]}
             for user_id, (current, wanted) in changes.items() if current is None and wanted]
    updated = {user_id: wanted for user_id, (current, wanted) in changes.items()
               if current and wanted and current != wanted}
    removed = [user_id for user_id, (current, wanted) in changes.items() if current and not wanted]
    if added:
        connection.execute(insert(members), added)
    for user_id, (role, agent_roles) in updated.items():
        connection.execute(update(members).where(in_default, members.c.user_id == user_id)
                           .values(role=role, agent_roles=agent_roles))
    for i in range(0, len(removed), _DELETE_CHUNK):
        connection.execute(delete(members).where(
            in_default, members.c.user_id.in_(removed[i:i + _DELETE_CHUNK])))
    return {"added": len(added), "updated": len(updated), "removed": len(removed)}


def _current_membership(connection: Connection, user_id: str) -> Membership:
    members = WorkspaceMember.__table__
    row = connection.execute(
        select(members.c.role, members.c.agent_roles)
        .where(members.c.workspace_id == DEFAULT_WORKSPACE_ID, members.c.user_id == user_id)
    ).first()
    return tuple(row) if row else None


@event.listens_for(User, "after_insert")
def _member_for_new_user(mapper: Mapper, connection: Connection, user: User) -> None:
    _write_members(connection, {user.user_id: (None, _membership(user.roles))})


@event.listens_for(User, "after_update")
def _member_for_changed_roles(mapper: Mapper, connection: Connection, user: User) -> None:
    if attributes.get_history(user, "roles").has_changes():
        _write_members(connection, {user.user_id: (_current_membership(connection, user.user_id),
                                                   _membership(user.roles))})


def sync_default_members(session: Session) -> Dict[str, int]:
    """Make every user's membership of the default workspace match users.roles.

    Reads users and members as plain rows, since widget visitors can make users a
    large table, and writes only what differs. Returns how many memberships were
    added, updated and removed. Safe to run on every start.
    """
    connection = session.connection()
    members = WorkspaceMember.__table__
    current = {user_id: (role, agent_roles) for user_id, role, agent_roles in connection.execute(
        select(members.c.user_id, members.c.role, members.c.agent_roles)
        .where(members.c.workspace_id == DEFAULT_WORKSPACE_ID))}
    users = User.__table__
    changes = {}
    for user_id, raw_roles in connection.execute(select(users.c.user_id, users.c.roles)):
        wanted = _membership(raw_roles)
        if current.get(user_id) != wanted:
            changes[user_id] = (current.get(user_id), wanted)
    counts = _write_members(connection, changes)
    session.commit()
    return counts


def run_member_sync(db_client: Optional["DatabaseClient"]) -> None:
    """sync_default_members() at startup. Never raises."""
    session = db_client.get_session() if db_client else None
    if not session:
        return
    try:
        counts = sync_default_members(session)
        if any(counts.values()):
            logger.info("Default workspace members: %s added, %s updated, %s removed",
                        counts["added"], counts["updated"], counts["removed"])
    except Exception as e:
        session.rollback()
        logger.error("Could not sync the default workspace members: %s", e)
    finally:
        session.close()


def workspace_of_project(session: Session, project_id: Optional[int]) -> Optional[int]:
    """The project's workspace, or None for no project or an unknown one."""
    if project_id is None:
        return None
    return session.query(Project.workspace_id).filter(Project.id == project_id).scalar()
