"""
Platform → Model groups: the installation's model groups, for platform administrators
(shared/utils/model_groups.py says what a group is).

- A group's name is fixed once it is made, as agents hold it; everything else changes.
- A group that agents name keeps at least one model and its Own servers only, and is
  deleted only together with moving those agents, of every workspace, to another group.
  They may leave a group with Own servers only for another such group and no other.
- The whole list of models is sent and checked on every save.

Every change is in the audit log with the group before and after it, and a moved agent
gets a version and an entry of its own, in its workspace.
server/dashboard_authz.py makes /dashboard/api/platform/ platform-only, and each
route checks it again.
"""

import json
import logging
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, BackgroundTasks, Body, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from server.member_routes import open_session
from server.platform_workspace_routes import _platform_admin
from server.request_context import RequestContext
from shared.utils import audit_service
from shared.utils.agent_config_identity import add_version
from shared.utils.model_groups import (
    NAME, NEEDS, PREFIX, ModelGroupError, agents_of, check_model_name, check_models, known_abilities, models_of,
    own_server_refusal, workspace_names,
)
from shared.utils.models import AgentConfig, ModelGroup

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard/api/platform/model-groups", tags=["Dashboard - Platform"])

LABEL_MAX = 100
DESCRIPTION_MAX = 500
FLAGS = ("needs_tools", "needs_images", "own_servers_only")


def _content(group: ModelGroup) -> Dict[str, Any]:
    """The group as the audit log keeps it."""
    return {"name": group.name, "label": group.label, "description": group.description or "",
            **{flag: bool(getattr(group, flag)) for flag in FLAGS}, "models": models_of(group)}


def _describe(session: Session, group: ModelGroup) -> Dict[str, Any]:
    agents = agents_of(session, group)
    workspaces = workspace_names(session, agents)
    content = _content(group)
    content["models"] = [{**m, "known": known_abilities(m["model"])} for m in content["models"]]
    return {**content, "agents": [{"name": a.name, "workspace": workspaces.get(a.project_id)} for a in agents],
            "updated_by": group.updated_by,
            "updated_at": group.updated_at.isoformat() if group.updated_at else None}


def _one(session: Session, name: str) -> ModelGroup:
    group = session.query(ModelGroup).filter(ModelGroup.name == name).first()
    if group is None:
        raise HTTPException(status_code=404, detail="Model group not found")
    return group


def _text(body: Dict[str, Any], field: str, longest: int, required: bool) -> str:
    value = body[field].strip() if isinstance(body.get(field), str) else ""
    if len(value) > longest or (required and not value):
        raise HTTPException(status_code=400, detail=f"{field} must be {'1 to' if required else 'at most'} "
                                                    f"{longest} characters")
    return value


def _apply(group: ModelGroup, body: Dict[str, Any], agents: List[AgentConfig], changed_by: str) -> None:
    """Puts what the body gives into the group and checks the whole; 400 or 409."""
    if "label" in body:
        group.label = _text(body, "label", LABEL_MAX, required=True)
    if "description" in body:
        group.description = _text(body, "description", DESCRIPTION_MAX, required=False) or None
    for flag in FLAGS:
        if flag in body:
            if not isinstance(body[flag], bool):
                raise HTTPException(status_code=400, detail=f"{flag} must be true or false")
            if flag == "own_servers_only" and agents and group.own_servers_only and not body[flag]:
                raise HTTPException(status_code=409, detail="Agents use this group because its models run on your "
                                                            "own servers; move them to another group first")
            setattr(group, flag, body[flag])
    needs = [need for need in NEEDS if getattr(group, f"needs_{need}")]
    try:
        models = check_models(body.get("models", models_of(group)), needs, bool(group.own_servers_only))
    except ModelGroupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if agents and not models:
        raise HTTPException(status_code=409, detail="Agents use this group; it needs at least one model")
    group.models = json.dumps(models)
    group.updated_by = changed_by


def _audit(ctx: RequestContext, action: str, name: str, details: Dict[str, Any], request: Request) -> None:
    audit_service.log(ctx.user_id, action, audit_service.RESOURCE_MODEL_GROUP, resource_id=name,
                      details=details, request=request)


# Plain functions, not async: checking a list may resolve a host's address


@router.get("")
def list_model_groups(ctx: RequestContext = Depends(_platform_admin)):
    """Every group with its models in order and the agents that name it."""
    session = open_session()
    try:
        return {"groups": [_describe(session, g) for g in session.query(ModelGroup).order_by(ModelGroup.id)]}
    finally:
        session.close()


@router.get("/check")
def check_model(model: str = Query(...), ctx: RequestContext = Depends(_platform_admin)):
    """What is known of a model before it is put in a group: what it can do (null where
    LiteLLM doesn't say), and why it is not on the installation's own servers (null when
    it is)."""
    try:
        name = check_model_name(model)
    except ModelGroupError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"model": name, "known": known_abilities(name), "own_server_refusal": own_server_refusal(name)}


@router.post("")
def create_model_group(request: Request, body: Dict[str, Any] = Body(...),
                       ctx: RequestContext = Depends(_platform_admin)):
    """Create a group: name and label, and optionally description, needs_tools,
    needs_images, own_servers_only and models (a list of {model, confirmed})."""
    name = body.get("name")
    if not isinstance(name, str) or not NAME.match(name):
        raise HTTPException(status_code=400, detail="name must be 1 to 40 lowercase letters, digits "
                                                    "and hyphens, starting and ending with a letter or digit")
    session = open_session()
    try:
        # An agent can hold the name already, from an import or a rollback
        group = ModelGroup(name=name, label=name, models="[]", needs_tools=False, needs_images=False,
                           own_servers_only=False)
        _apply(group, {"label": "", **body}, [], ctx.user_id)
        session.add(group)
        session.commit()
        result = _describe(session, group)
        new = _content(group)
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Another group has this name")
    finally:
        session.close()
    _audit(ctx, audit_service.ACTION_MODEL_GROUP_CREATE, name, {"new": new}, request)
    return {"group": result}


@router.put("/{name}")
def update_model_group(name: str, request: Request, body: Dict[str, Any] = Body(...),
                       ctx: RequestContext = Depends(_platform_admin)):
    """Change any of label, description, needs_tools, needs_images, own_servers_only and
    models, which replaces the whole list."""
    session = open_session()
    try:
        group = _one(session, name)
        old = _content(group)
        _apply(group, body, agents_of(session, group), ctx.user_id)
        new = _content(group)
        session.commit()
        result = _describe(session, group)
    finally:
        session.close()
    if new != old:
        _audit(ctx, audit_service.ACTION_MODEL_GROUP_UPDATE, name, {"old": old, "new": new}, request)
    return {"group": result}


@router.delete("/{name}")
def delete_model_group(name: str, request: Request, background: BackgroundTasks,
                       move_to: Optional[str] = Query(None, description="The group its agents move to"),
                       ctx: RequestContext = Depends(_platform_admin)):
    """Delete a group. 409 while agents name it, unless move_to names the group they all
    move to."""
    session = open_session()
    try:
        group = _one(session, name)
        agents = agents_of(session, group)
        if agents:
            if not move_to:
                raise HTTPException(status_code=409, detail=f"{len(agents)} agent(s) use this group; "
                                                            "give move_to, the group they move to")
            target = _one(session, move_to)
            if target.id == group.id or not models_of(target):
                raise HTTPException(status_code=400, detail="move_to must be another group, with models")
            if group.own_servers_only and not target.own_servers_only:
                raise HTTPException(status_code=409, detail="Agents of a group with Own servers only move to "
                                                            "another such group")
            for agent in agents:
                agent.model_name = PREFIX + target.name
                session.flush()
                add_version(session, agent, "update", ctx.user_id)
        old = _content(group)
        moved = [(a.id, a.name) for a in agents]
        session.delete(group)
        session.commit()
    finally:
        session.close()
    details: Dict[str, Any] = {"old": old}
    if moved:
        details.update(agents_moved_to=move_to, agents=[agent_name for _, agent_name in moved])
    _audit(ctx, audit_service.ACTION_MODEL_GROUP_DELETE, name, details, request)
    for config_id, agent_name in moved:
        audit_service.log(ctx.user_id, audit_service.ACTION_AGENT_UPDATE, audit_service.RESOURCE_AGENT,
                          resource_id=agent_name, details={"config_id": config_id, "model_name": PREFIX + str(move_to),
                                                           "via": "model_group"}, request=request)
    background.add_task(_reload_agents, [agent_name for _, agent_name in moved])
    return {"deleted": name, "agents_moved": len(moved)}


def _reload_agents(names: List[str]) -> None:
    """The agent server runs the agents with their new model; one it doesn't reach is
    read anew when it starts."""
    from shared.utils.utils import get_adk_config
    config = get_adk_config()
    for name in names:
        try:
            httpx.post(f"http://{config['adk_host']}:{config['adk_port']}/api/reload-agent/{name}", timeout=5.0)
        except httpx.HTTPError as e:
            logger.info("Agent '%s' not reloaded after its group was deleted: %s", name, e)
            return
