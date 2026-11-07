"""
Workspaces (documents/design/2.0-plan.md): memberships, and the lookups that scope a
request to its workspace (server/request_context.py).

Every 1.x row belongs to the default workspace (V046). Until memberships are edited on
their own (phase 3), a user's membership of it is derived from users.roles
(documents/design/workspaces.md, D3):
- the admin role makes the user an owner, anything else a member;
- agent_roles are the user's roles without admin;
- a widget visitor or a pending sign-up is no member at all. A widget reaches its
  workspace through its key's project, and a pending user has not been let in yet;
  approving them replaces 'pending' and so adds the membership.

A user the platform creates on its own, for an agent of another workspace (an end
user an API client runs through /run_sse, an A2A or a Slack user), joins that
workspace instead: get_or_create_user() sets home_workspace_id on the new User, and
the membership is derived in that workspace. Such a user, a member somewhere but not of
the default workspace, isn't added to it. Until phase 3 every membership's
agent_roles follow users.roles.

sync_default_members() brings every membership in line when the auth server starts,
which also adds the users of an installation that has just migrated. Listeners on the
User model do the same for each user inserted, or whose roles change, after that.
Both write through _write_members(). models.py imports this module, so the listeners
are registered wherever users are written.

caller_access() decides who may use a workspace's agents; the RBAC check
(rbac_middleware.py) and the entry points ask it.
"""

import json
import logging
import re
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from sqlalchemy import delete, event, insert, or_, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.orm import Mapper, Session, attributes

from .models import (
    DEFAULT_WORKSPACE_ID, AgentConfig, AgentConfigVersion, FileSearchStore, Project, TestCase,
    User, WidgetApiKey, Workspace, WorkspaceMember, parse_roles,
)

if TYPE_CHECKING:
    from .database_client import DatabaseClient

logger = logging.getLogger(__name__)

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
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


def _write_members(connection: Connection, changes: Dict[str, Tuple[Membership, Membership]],
                   workspace_id: int = DEFAULT_WORKSPACE_ID) -> Dict[str, int]:
    """Apply {user_id: (current, wanted)} to the workspace; returns counts."""
    members = WorkspaceMember.__table__
    in_workspace = members.c.workspace_id == workspace_id
    added = [{"workspace_id": workspace_id, "user_id": user_id,
              "role": wanted[0], "agent_roles": wanted[1]}
             for user_id, (current, wanted) in changes.items() if current is None and wanted]
    updated = {user_id: wanted for user_id, (current, wanted) in changes.items()
               if current and wanted and current != wanted}
    removed = [user_id for user_id, (current, wanted) in changes.items() if current and not wanted]
    if added:
        connection.execute(insert(members), added)
    for user_id, (role, agent_roles) in updated.items():
        connection.execute(update(members).where(in_workspace, members.c.user_id == user_id)
                           .values(role=role, agent_roles=agent_roles))
    for i in range(0, len(removed), _DELETE_CHUNK):
        connection.execute(delete(members).where(
            in_workspace, members.c.user_id.in_(removed[i:i + _DELETE_CHUNK])))
    return {"added": len(added), "updated": len(updated), "removed": len(removed)}


def _current_membership(connection: Connection, user_id: str) -> Membership:
    members = WorkspaceMember.__table__
    row = connection.execute(
        select(members.c.role, members.c.agent_roles)
        .where(members.c.workspace_id == DEFAULT_WORKSPACE_ID, members.c.user_id == user_id)
    ).first()
    return tuple(row) if row else None


def _members_elsewhere(connection: Connection, user_ids: Optional[List[str]] = None) -> Set[str]:
    """Those of the users (all, for None) with a membership outside the default workspace."""
    members = WorkspaceMember.__table__
    query = select(members.c.user_id).where(members.c.workspace_id != DEFAULT_WORKSPACE_ID)
    if user_ids is not None:
        query = query.where(members.c.user_id.in_(user_ids))
    return {row[0] for row in connection.execute(query)}


@event.listens_for(User, "after_insert")
def _member_for_new_user(mapper: Mapper, connection: Connection, user: User) -> None:
    workspace_id = getattr(user, "home_workspace_id", None) or DEFAULT_WORKSPACE_ID
    _write_members(connection, {user.user_id: (None, _membership(user.roles))}, workspace_id)


@event.listens_for(User, "after_update")
def _member_for_changed_roles(mapper: Mapper, connection: Connection, user: User) -> None:
    if not attributes.get_history(user, "roles").has_changes():
        return
    wanted = _membership(user.roles)
    current = _current_membership(connection, user.user_id)
    elsewhere = bool(_members_elsewhere(connection, [user.user_id]))
    if current or not elsewhere:
        _write_members(connection, {user.user_id: (current, wanted)})
    if elsewhere and wanted:
        members = WorkspaceMember.__table__
        connection.execute(update(members).where(
            members.c.user_id == user.user_id, members.c.workspace_id != DEFAULT_WORKSPACE_ID,
        ).values(agent_roles=wanted[1]))
    elif elsewhere:
        # pending or widget: no member anywhere, not only in the default workspace
        _remove_elsewhere(connection, [user.user_id])


def _remove_elsewhere(connection: Connection, user_ids: List[str]) -> int:
    """Delete the users' memberships outside the default workspace; returns how many."""
    members = WorkspaceMember.__table__
    removed = 0
    for i in range(0, len(user_ids), _DELETE_CHUNK):
        removed += connection.execute(delete(members).where(
            members.c.workspace_id != DEFAULT_WORKSPACE_ID,
            members.c.user_id.in_(user_ids[i:i + _DELETE_CHUNK]))).rowcount
    return removed


def sync_default_members(session: Session) -> Dict[str, int]:
    """Make every user's membership of the default workspace match users.roles.

    Reads users and members as plain rows, since widget visitors can make users a
    large table, and writes only what differs. A user who is a member of another
    workspace only is left out of it, and one whose roles make them no member
    (pending, widget) loses the memberships of the other workspaces too. Returns how
    many memberships were added, updated and removed. Safe to run on every start.
    """
    connection = session.connection()
    members = WorkspaceMember.__table__
    current = {user_id: (role, agent_roles) for user_id, role, agent_roles in connection.execute(
        select(members.c.user_id, members.c.role, members.c.agent_roles)
        .where(members.c.workspace_id == DEFAULT_WORKSPACE_ID))}
    elsewhere = _members_elsewhere(connection)
    users = User.__table__
    changes = {}
    no_member = []
    for user_id, raw_roles in connection.execute(select(users.c.user_id, users.c.roles)):
        wanted = _membership(raw_roles)
        if wanted is None and user_id in elsewhere:
            no_member.append(user_id)
        if user_id in elsewhere and user_id not in current:
            continue
        if current.get(user_id) != wanted:
            changes[user_id] = (current.get(user_id), wanted)
    counts = _write_members(connection, changes)
    counts["removed"] += _remove_elsewhere(connection, no_member)
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


def workspace_is_open(session: Session, workspace_id: int) -> bool:
    """True if the workspace exists and is not deleted."""
    return session.query(Workspace.id).filter(
        Workspace.id == workspace_id, Workspace.deleted_at.is_(None)).first() is not None


def member_role(session: Session, workspace_id: int, user_id: str) -> Optional[str]:
    """The user's role in the workspace, or None when they are no member."""
    return session.query(WorkspaceMember.role).filter(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user_id).scalar()


def first_workspace_of(session: Session, user_id: str) -> Optional[int]:
    """The open workspace with the lowest id the user is a member of, or None."""
    return session.query(WorkspaceMember.workspace_id).join(
        Workspace, Workspace.id == WorkspaceMember.workspace_id).filter(
        WorkspaceMember.user_id == user_id, Workspace.deleted_at.is_(None)).order_by(
        WorkspaceMember.workspace_id).limit(1).scalar()


def project_in_workspace(session: Session, workspace_id: int,
                         project_id: Optional[int]) -> Optional[Project]:
    """The project, if it belongs to the workspace; None otherwise, for the caller to 404."""
    if project_id is None:
        return None
    return session.query(Project).filter(
        Project.id == project_id, Project.workspace_id == workspace_id).first()


def agent_workspace(session: Session, agent_name: str) -> Optional[int]:
    """The workspace of the agent's project, or None for an agent with no config."""
    return session.query(Project.workspace_id).join(
        AgentConfig, AgentConfig.project_id == Project.id).filter(
        AgentConfig.name == agent_name).limit(1).scalar()


def config_workspace(session: Session, config_id: int) -> Optional[int]:
    """The workspace of the agent config, or None for no such config."""
    return session.query(Project.workspace_id).join(
        AgentConfig, AgentConfig.project_id == Project.id).filter(
        AgentConfig.id == config_id).scalar()


def version_workspace(session: Session, version_id: int) -> Optional[int]:
    """The workspace of the agent a config version belongs to, or None."""
    return session.query(Project.workspace_id).join(
        AgentConfig, AgentConfig.project_id == Project.id).join(
        AgentConfigVersion, AgentConfigVersion.agent_config_id == AgentConfig.id).filter(
        AgentConfigVersion.id == version_id).scalar()


def store_workspace(session: Session, store_name: str) -> Optional[int]:
    """The workspace of a file search store's project, or None for an unknown store."""
    return session.query(Project.workspace_id).join(
        FileSearchStore, FileSearchStore.project_id == Project.id).filter(
        FileSearchStore.store_name == store_name).scalar()


def test_case_workspace(session: Session, test_case_id: int) -> Optional[int]:
    """The workspace of an eval test case, or None for no such case."""
    return session.query(TestCase.workspace_id).filter(TestCase.id == test_case_id).scalar()


def agent_names_of(workspace_id: int):
    """A subquery of the names of the workspace's agents, for an IN filter."""
    return select(AgentConfig.name).join(Project, AgentConfig.project_id == Project.id).where(
        Project.workspace_id == workspace_id).scalar_subquery()


def rows_of(model: type, workspace_id: Optional[int]) -> tuple:
    """Filters keeping a table with a workspace_id column to the workspace's rows, or
    none for None (every workspace). On a log table a row with no workspace is about an
    agent with no config or about the installation, or the backfill hasn't reached it;
    it counts as the default workspace's, as a hardcoded agent does."""
    if workspace_id is None:
        return ()
    if workspace_id == DEFAULT_WORKSPACE_ID:
        return (or_(model.workspace_id == workspace_id, model.workspace_id.is_(None)),)
    return (model.workspace_id == workspace_id,)


def in_projects_of(column: ColumnElement, workspace_id: Optional[int]) -> tuple:
    """Filters keeping rows whose project_id `column` is one of the workspace's
    projects, or none for None. A row with no project counts as the default
    workspace's."""
    if workspace_id is None:
        return ()
    projects = column.in_(select(Project.id).where(Project.workspace_id == workspace_id))
    if workspace_id == DEFAULT_WORKSPACE_ID:
        return (or_(projects, column.is_(None)),)
    return (projects,)


def agents_outside(session: Session, workspace_id: int, names: List[str]) -> List[str]:
    """Those of the names that aren't agents of the workspace (D1: parent_agents stay
    within it). A name with no config counts as the default workspace's: hardcoded
    agents belong to the installation, and 1.x lets parent_agents name anything."""
    found = dict(session.query(AgentConfig.name, Project.workspace_id).join(
        Project, AgentConfig.project_id == Project.id).filter(
        AgentConfig.name.in_(names)).all()) if names else {}
    return [name for name in names
            if found.get(name, DEFAULT_WORKSPACE_ID) != workspace_id]


def missing_from(session: Session, workspace_id: int, *, project_id: Optional[int] = None,
                 agent_name: Optional[str] = None) -> Optional[str]:
    """"Project" or "Agent" when the one named isn't the workspace's, for the caller to
    404 on; None when both are. An agent with no config counts as the default
    workspace's."""
    if project_id is not None:
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            return "Project"
        if workspace_of_project(session, project_id) != workspace_id:
            return "Project"
    if agent_name is not None and agents_outside(session, workspace_id, [agent_name]):
        return "Agent"
    return None


# Callers that run an agent on their own: the trigger runner, for the trigger's agent.
# Eval runs skip the RBAC check altogether (rbac_callback._should_skip_rbac_check).
SYSTEM_CALLERS = {"trigger_runner"}

_WIDGET_USER = re.compile(r"^widget_(\d+)_")


def is_reserved_user_id(user_id: str) -> bool:
    """True for an id the server gives out on its own: a system caller, or a widget
    visitor's widget_<key id>_<uid> (any id starting widget_, which
    UserService.get_or_create_user gives the widget role). A sign-in or a new account
    can't take one."""
    return user_id in SYSTEM_CALLERS or user_id.startswith("widget_")


def caller_access(session: Session, user: User,
                  workspace_id: Optional[int]) -> Optional[Tuple[bool, List[str]]]:
    """Whether the user may use the workspace's agents: None when not, else
    (passes every agent's allowed_for_roles, the roles matched against them).

    - A platform administrator (the admin role) and an owner or admin of the
      workspace pass allowed_for_roles: a user whose only role is admin has no
      agent_roles at all.
    - A member is matched on their agent_roles.
    - A widget visitor (widget_<key id>_..., with the widget role only the server
      gives out) reaches the workspace of their key's project, and a system caller
      any workspace, both on users.roles. Neither id can be signed in with
      (is_reserved_user_id).
    """
    roles = user.get_roles()
    if "admin" in roles:
        return True, roles
    if workspace_id is None:
        return None
    widget = _WIDGET_USER.match(user.user_id)
    if widget and "widget" in roles:
        key_workspace = session.query(Project.workspace_id).join(
            WidgetApiKey, WidgetApiKey.project_id == Project.id).filter(
            WidgetApiKey.id == int(widget.group(1))).scalar()
        return (False, roles) if key_workspace == workspace_id else None
    if user.user_id in SYSTEM_CALLERS:
        return False, roles
    member = session.query(WorkspaceMember.role, WorkspaceMember.agent_roles).filter(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user.user_id).first()
    if member is None:
        return None
    return member.role in (ROLE_OWNER, ROLE_ADMIN), parse_roles(member.agent_roles)


def roles_allow(access: Optional[Tuple[bool, List[str]]], allowed_for_roles: Optional[str]) -> bool:
    """True if a caller with this caller_access() may use an agent with these
    allowed_for_roles (stored JSON; none, or invalid, means admin-only)."""
    if access is None:
        return False
    passes_roles, roles = access
    if passes_roles:
        return True
    try:
        allowed = json.loads(allowed_for_roles) if allowed_for_roles else []
    except (TypeError, ValueError):
        allowed = []
    return any(role in roles for role in allowed)
