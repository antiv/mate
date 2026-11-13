"""
Workspaces (documents/design/2.0-plan.md): memberships, and the lookups that scope a
request to its workspace (server/request_context.py).

A user's role in a workspace (owner, admin or member) and their agent roles there,
matched against an agent's allowed_for_roles, are their workspace_members row. Owners
edit them on the Members page (server/member_routes.py). users.roles keeps only the
platform roles (PLATFORM_ROLES): admin (platform administrator), pending (not let in
yet) and widget (a widget visitor). The server's own callers (SYSTEM_CALLERS) belong
to no workspace and keep their agent roles in users.roles.

Until phase 3e every membership was derived from users.roles (D3). What is left of it:
- A user inserted with agent roles in users.roles, as 1.x code and the Users page
  create them, becomes a member of their home workspace with those agent roles, and
  users.roles keeps the platform ones (_member_for_new_user). The home workspace is
  the default one, or the workspace a user the platform creates for an agent of
  another workspace joins (get_or_create_user sets home_workspace_id). An admin
  becomes its owner, anyone else a member.
- A pending user or a widget visitor is no member anywhere: becoming one removes every
  membership. Approving a pending user who is a member nowhere adds them to the
  default workspace (_member_for_changed_roles).
- Agent roles written to users.roles later move to the user's membership of the
  default workspace, which a user who is a member elsewhere only joins for them.
- Losing the admin role takes away the default workspace's ownership that being a
  platform administrator gave (1.x's admins are its owners): an owner of it becomes a
  member, its last owner too, since taking a platform administrator's rights away
  comes first and platform administrators (the built-in account always) manage the
  default workspace. Owners of other workspaces stay owners.
- move_roles_to_members() runs when the auth server starts. Once per installation it
  derives the memberships from users.roles as 2.0 before 3e did, which also adds the
  users of a 1.x installation, and empties users.roles of agent roles. Every start it
  removes the memberships of pending users and widget visitors.

caller_access() decides who may use a workspace's agents; the RBAC check
(rbac_middleware.py) and the entry points ask it.
"""

import json
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional, Set, Tuple

from sqlalchemy import delete, event, insert, or_, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.selectable import ScalarSelect
from sqlalchemy.orm import Mapper, Session, attributes

from .models import (
    DEFAULT_WORKSPACE_ID, AgentConfig, AgentConfigVersion, FileSearchStore, Project, SystemSetting,
    TestCase, User, WidgetApiKey, Workspace, WorkspaceMember, parse_roles,
)

if TYPE_CHECKING:
    from .database_client import DatabaseClient

logger = logging.getLogger(__name__)

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
MEMBER_ROLES = (ROLE_OWNER, ROLE_ADMIN, ROLE_MEMBER)
# The roles users.roles keeps; every other role is an agent role, per membership
PLATFORM_ROLES = ("admin", "pending", "widget")
# Users with one of these roles belong to no workspace (see the module docstring)
NON_MEMBER_ROLES = {"widget", "pending"}
# The agent roles of a member added with none given (workspace_members' default)
DEFAULT_AGENT_ROLES = ["user"]
# system_settings row set once move_roles_to_members() has moved users.roles
ROLES_MOVED_SETTING = "workspaces.roles_moved_to_members"

# (role, agent_roles as stored) of a membership; None for no membership
Membership = Optional[Tuple[str, str]]

_DELETE_CHUNK = 500


def membership_from_roles(roles: List[str]) -> Optional[Tuple[str, List[str]]]:
    """(role, agent_roles) for a user who has these roles in users.roles, as phase 2
    derived it, or None when such a user is no member."""
    if NON_MEMBER_ROLES.intersection(roles):
        return None
    role = ROLE_OWNER if "admin" in roles else ROLE_MEMBER
    return role, [r for r in roles if r != "admin"]


def platform_roles(roles: List[str]) -> List[str]:
    """The roles users.roles keeps."""
    return [r for r in roles if r in PLATFORM_ROLES]


def agent_roles(roles: List[str]) -> List[str]:
    """The roles that belong to a membership."""
    return [r for r in roles if r not in PLATFORM_ROLES]


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
    for user_id, (role, roles) in updated.items():
        connection.execute(update(members).where(in_workspace, members.c.user_id == user_id)
                           .values(role=role, agent_roles=roles))
    for i in range(0, len(removed), _DELETE_CHUNK):
        connection.execute(delete(members).where(
            in_workspace, members.c.user_id.in_(removed[i:i + _DELETE_CHUNK])))
    return {"added": len(added), "updated": len(updated), "removed": len(removed)}


def _members_elsewhere(connection: Connection, user_ids: Optional[List[str]] = None) -> Set[str]:
    """Those of the users (all, for None) with a membership outside the default workspace."""
    members = WorkspaceMember.__table__
    query = select(members.c.user_id).where(members.c.workspace_id != DEFAULT_WORKSPACE_ID)
    if user_ids is not None:
        query = query.where(members.c.user_id.in_(user_ids))
    return {row[0] for row in connection.execute(query)}


def _keep_platform_roles(connection: Connection, user: User) -> List[str]:
    """Leave users.roles the platform roles, unless the user is one of the server's own
    callers; returns the agent roles taken out."""
    roles = parse_roles(user.roles)
    taken = agent_roles(roles)
    if not taken or is_reserved_user_id(user.user_id):
        return []
    kept = json.dumps(platform_roles(roles))
    users = User.__table__
    connection.execute(update(users).where(users.c.user_id == user.user_id).values(roles=kept))
    attributes.set_committed_value(user, "roles", kept)
    return taken


@event.listens_for(User, "after_insert")
def _member_for_new_user(mapper: Mapper, connection: Connection, user: User) -> None:
    workspace_id = getattr(user, "home_workspace_id", None) or DEFAULT_WORKSPACE_ID
    _write_members(connection, {user.user_id: (None, _membership(user.roles))}, workspace_id)
    _keep_platform_roles(connection, user)


@event.listens_for(User, "after_update")
def _member_for_changed_roles(mapper: Mapper, connection: Connection, user: User) -> None:
    history = attributes.get_history(user, "roles")
    if not history.has_changes():
        return
    before = parse_roles(history.deleted[0]) if history.deleted else []
    roles = parse_roles(user.roles)
    members = WorkspaceMember.__table__
    if NON_MEMBER_ROLES.intersection(roles):
        # pending or widget: no member anywhere
        connection.execute(delete(members).where(members.c.user_id == user.user_id))
        return
    if is_reserved_user_id(user.user_id):
        return
    taken = _keep_platform_roles(connection, user)
    member_anywhere = connection.execute(
        select(members.c.id).where(members.c.user_id == user.user_id).limit(1)).first()
    if member_anywhere is None:
        # An approved sign-up joins the default workspace
        role = ROLE_OWNER if "admin" in roles else ROLE_MEMBER
        connection.execute(insert(members).values(
            workspace_id=DEFAULT_WORKSPACE_ID, user_id=user.user_id, role=role,
            agent_roles=json.dumps(taken or ([] if "admin" in roles else DEFAULT_AGENT_ROLES))))
        return
    in_default = (members.c.workspace_id == DEFAULT_WORKSPACE_ID, members.c.user_id == user.user_id)
    if "admin" in before and "admin" not in roles:
        connection.execute(update(members).where(*in_default, members.c.role == ROLE_OWNER)
                           .values(role=ROLE_MEMBER))
    if taken:
        moved = connection.execute(update(members).where(*in_default)
                                   .values(agent_roles=json.dumps(taken))).rowcount
        if not moved:
            # A member of another workspace only: 1.x code means the default one
            connection.execute(insert(members).values(
                workspace_id=DEFAULT_WORKSPACE_ID, user_id=user.user_id, role=ROLE_MEMBER,
                agent_roles=json.dumps(taken)))


def _derive_default_members(connection: Connection, users: List[Tuple[str, Optional[str]]],
                            elsewhere: Set[str]) -> Dict[str, int]:
    """Phase 2's sync: every user's membership of the default workspace from users.roles.
    A user who is a member of another workspace only is left out of it."""
    members = WorkspaceMember.__table__
    current = {user_id: (role, roles) for user_id, role, roles in connection.execute(
        select(members.c.user_id, members.c.role, members.c.agent_roles)
        .where(members.c.workspace_id == DEFAULT_WORKSPACE_ID))}
    changes = {}
    for user_id, raw_roles in users:
        if user_id in elsewhere and user_id not in current:
            continue
        wanted = _membership(raw_roles)
        if current.get(user_id) != wanted:
            changes[user_id] = (current.get(user_id), wanted)
    return _write_members(connection, changes)


def move_roles_to_members(session: Session) -> Dict[str, int]:
    """Memberships from users.roles, once per installation; then every start, no
    membership for a pending user or a widget visitor.

    The first run derives every user's membership of the default workspace as phase 2
    did, and leaves users.roles the platform roles (the server's own callers excepted),
    so that memberships hold the agent roles from then on. Reads users as plain rows,
    since widget visitors can make the table large. Returns how many memberships were
    added, updated and removed, and how many users' roles were moved."""
    connection = session.connection()
    users = User.__table__
    rows = [(user_id, raw) for user_id, raw in connection.execute(select(users.c.user_id, users.c.roles))]
    elsewhere = _members_elsewhere(connection)
    counts = {"added": 0, "updated": 0, "removed": 0, "moved": 0}
    if session.get(SystemSetting, ROLES_MOVED_SETTING) is None:
        counts.update(_derive_default_members(connection, rows, elsewhere))
        for user_id, raw in rows:
            roles = parse_roles(raw)
            if agent_roles(roles) and not is_reserved_user_id(user_id):
                connection.execute(update(users).where(users.c.user_id == user_id)
                                   .values(roles=json.dumps(platform_roles(roles))))
                counts["moved"] += 1
        session.add(SystemSetting(setting_key=ROLES_MOVED_SETTING,
                                  value=datetime.now(timezone.utc).isoformat(), updated_by="system"))
    no_member = [user_id for user_id, raw in rows if NON_MEMBER_ROLES.intersection(parse_roles(raw))]
    members = WorkspaceMember.__table__
    for i in range(0, len(no_member), _DELETE_CHUNK):
        counts["removed"] += connection.execute(delete(members).where(
            members.c.user_id.in_(no_member[i:i + _DELETE_CHUNK]))).rowcount
    session.commit()
    return counts


def run_member_sync(db_client: Optional["DatabaseClient"]) -> None:
    """move_roles_to_members() at startup. Never raises."""
    session = db_client.get_session() if db_client else None
    if not session:
        return
    try:
        counts = move_roles_to_members(session)
        if any(counts.values()):
            logger.info("Workspace members: %s added, %s updated, %s removed; roles of %s "
                        "users moved to their memberships", counts["added"], counts["updated"],
                        counts["removed"], counts["moved"])
    except Exception as e:
        session.rollback()
        logger.error("Could not sync the workspace members: %s", e)
    finally:
        session.close()


def agent_roles_in(session: Session, workspace_id: Optional[int], user_id: str) -> List[str]:
    """The user's agent roles in the workspace; none when they are no member."""
    if workspace_id is None:
        return []
    raw = session.query(WorkspaceMember.agent_roles).filter(
        WorkspaceMember.workspace_id == workspace_id, WorkspaceMember.user_id == user_id).scalar()
    return parse_roles(raw) if raw else []


def workspace_of_project(session: Session, project_id: Optional[int]) -> Optional[int]:
    """The project's workspace, or None for no project or an unknown one."""
    if project_id is None:
        return None
    return session.query(Project.workspace_id).filter(Project.id == project_id).scalar()


def open_filters() -> tuple:
    """Filters keeping the workspaces that are neither closed (deleted_at) nor
    suspended (suspended_at, V048). Anything else stops: the dashboard, tokens,
    agents, widget keys, Slack, triggers and alert rules (phase 3c)."""
    return (Workspace.deleted_at.is_(None), Workspace.suspended_at.is_(None))


def workspace_is_open(session: Session, workspace_id: int) -> bool:
    """True if the workspace exists, and is neither closed nor suspended."""
    return session.query(Workspace.id).filter(
        Workspace.id == workspace_id, *open_filters()).first() is not None


def open_workspace_ids(session: Session) -> Set[int]:
    """The ids of every open workspace."""
    return {workspace_id for (workspace_id,) in session.query(Workspace.id).filter(*open_filters())}


def project_is_open(session: Session, project_id: Optional[int]) -> bool:
    """True if the project exists and its workspace is open: a widget key, Slack
    integration or trigger of a closed or suspended workspace stops with it."""
    return session.query(Project.id).join(Workspace, Workspace.id == Project.workspace_id).filter(
        Project.id == project_id, *open_filters()).first() is not None


def workspace_has_feature(session: Session, workspace_id: Optional[int], feature: str) -> bool:
    """True if a platform administrator enabled the platform-only feature (one of
    PLATFORM_FEATURES) for the workspace, and it is open."""
    raw = session.query(Workspace.platform_features).filter(
        Workspace.id == workspace_id, *open_filters()).scalar() if workspace_id is not None else None
    try:
        features = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        logger.warning("Invalid platform_features on workspace %s", workspace_id)
        return False
    return isinstance(features, list) and feature in features


def member_role(session: Session, workspace_id: int, user_id: str) -> Optional[str]:
    """The user's role in the workspace, or None when they are no member."""
    return session.query(WorkspaceMember.role).filter(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user_id).scalar()


def first_workspace_of(session: Session, user_id: str) -> Optional[int]:
    """The open workspace with the lowest id the user is a member of, or None."""
    return session.query(WorkspaceMember.workspace_id).join(
        Workspace, Workspace.id == WorkspaceMember.workspace_id).filter(
        WorkspaceMember.user_id == user_id, *open_filters()).order_by(
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


def _no_config_workspace(is_platform_admin: bool) -> Optional[int]:
    """The workspace an agent name with no config (a hardcoded agent, or one deleted
    since) belongs to for the caller. Such an agent belongs to the installation: a
    platform administrator reaches it from the default workspace, anyone else from
    none. The one rule for it (agent_workspace_for, agents_outside, missing_from)."""
    return DEFAULT_WORKSPACE_ID if is_platform_admin else None


def agent_workspace_for(session: Session, agent_name: str, is_platform_admin: bool) -> Optional[int]:
    """The workspace of the agent for this caller: its project's, and for a name with no
    config the default workspace for a platform administrator, else None."""
    workspace_id = agent_workspace(session, agent_name)
    return workspace_id if workspace_id is not None else _no_config_workspace(is_platform_admin)


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


def agent_names_of(workspace_id: int) -> ScalarSelect:
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


def agents_outside(session: Session, workspace_id: int, names: List[str], *,
                   is_platform_admin: bool) -> List[str]:
    """Those of the names that aren't agents of the workspace for this caller (D1:
    parent_agents stay within it). A name with no config is the default workspace's
    for a platform administrator only (_no_config_workspace)."""
    found = dict(session.query(AgentConfig.name, Project.workspace_id).join(
        Project, AgentConfig.project_id == Project.id).filter(
        AgentConfig.name.in_(names)).all()) if names else {}
    no_config = _no_config_workspace(is_platform_admin)
    return [name for name in names if found.get(name, no_config) != workspace_id]


def missing_from(session: Session, workspace_id: int, *, is_platform_admin: bool,
                 project_id: Optional[int] = None,
                 agent_name: Optional[str] = None) -> Optional[str]:
    """"Project" or "Agent" when the one named isn't the workspace's, for the caller to
    404 on; None when both are. An agent with no config is the default workspace's for
    a platform administrator only (_no_config_workspace)."""
    if project_id is not None:
        try:
            project_id = int(project_id)
        except (TypeError, ValueError):
            return "Project"
        if workspace_of_project(session, project_id) != workspace_id:
            return "Project"
    if agent_name is not None and agents_outside(session, workspace_id, [agent_name],
                                                 is_platform_admin=is_platform_admin):
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
    - No one uses the agents of a closed or suspended workspace, a platform
      administrator included.
    """
    if workspace_id is not None and not workspace_is_open(session, workspace_id):
        return None
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
