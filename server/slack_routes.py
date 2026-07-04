"""
Slack integration routes.

Receives Slack Events API callbacks, verifies the request signature, maps the
Slack (team, channel, thread) to a stable MATE conversation, runs the configured
agent, and posts the reply back into the thread. Agent [[CARD]] markers are
translated into Slack Block Kit; card button clicks arrive on /interactions and
are fed back to the agent.

The heavy lifting (agent invocation) is delegated to
`shared.utils.agent_invoke.run_agent_message`; this module is a thin Slack<->MATE
adapter. Configuration lives in the `channel_integrations` table.
"""

import hashlib
import hmac
import json
import logging
import re
import time
from collections import OrderedDict
from typing import Optional
from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response

from shared.utils.agent_invoke import run_agent_message
from shared.utils.database_client import get_database_client
from shared.utils.models import ChannelIntegration

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/integrations/slack", tags=["Slack Integration"])
dashboard_router = APIRouter(prefix="/dashboard/api", tags=["Dashboard - Channel Integrations"])


def _dashboard_auth():
    from server.auth import get_dashboard_auth_user
    return get_dashboard_auth_user

SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")

# Bounded set of recently handled Slack event ids — Slack retries deliveries, and
# we must not run the agent twice for the same message.
_seen_event_ids: "OrderedDict[str, float]" = OrderedDict()
_SEEN_MAX = 2000


def _already_handled(event_id: Optional[str]) -> bool:
    if not event_id:
        return False
    if event_id in _seen_event_ids:
        return True
    _seen_event_ids[event_id] = time.time()
    while len(_seen_event_ids) > _SEEN_MAX:
        _seen_event_ids.popitem(last=False)
    return False


def _get_integration(team_id: Optional[str]) -> Optional[ChannelIntegration]:
    """Look up the active Slack integration for a workspace."""
    db = get_database_client()
    session = db.get_session()
    if not session:
        return None
    try:
        query = session.query(ChannelIntegration).filter(
            ChannelIntegration.platform == "slack",
            ChannelIntegration.is_active.is_(True),
        )
        if team_id:
            query = query.filter(ChannelIntegration.team_id == team_id)
        integration = query.first()
        if integration:
            session.expunge(integration)
        return integration
    finally:
        session.close()


def _verify_signature(secret: str, timestamp: str, raw_body: bytes, signature: str) -> bool:
    """Verify a Slack request signature (v0 scheme)."""
    if not secret or not timestamp or not signature:
        return False
    try:
        # Reject stale requests (replay protection): older than 5 minutes.
        if abs(time.time() - int(timestamp)) > 60 * 5:
            return False
    except ValueError:
        return False
    basestring = f"v0:{timestamp}:{raw_body.decode('utf-8', errors='replace')}"
    computed = "v0=" + hmac.new(
        secret.encode("utf-8"), basestring.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(computed, signature)


# --------------------------------------------------------------------------- #
# Rich-card translation: agents emit [[CARD]]{json} markers that the web chat
# renders as interactive cards. Slack can't read those, so we translate them to
# Block Kit blocks (section + buttons). Mirrors _extractCards in
# static/js/widget/chat.js.
# --------------------------------------------------------------------------- #

_CARD_RE = re.compile(r"\[\[(CARD|APPOINTMENT)\]\][ \t]*\n?[ \t]*(?:```(?:json|JSON)?[ \t]*\n?)?\{")


def _balanced_end(text: str, start: int) -> int:
    """Index of the '}' that closes the object opened at `start`, or -1."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _extract_cards(text: str):
    """Return (cleaned_text, [card_dicts]) by stripping [[CARD]] markers."""
    cards, ranges = [], []
    for m in _CARD_RE.finditer(text):
        brace_start = text.index("{", m.start())
        end = _balanced_end(text, brace_start)
        if end == -1:
            continue
        try:
            data = json.loads(text[brace_start:end + 1])
        except json.JSONDecodeError:
            continue
        cards.append(data)
        end_ext = end + 1
        fence = re.match(r"[ \t]*\n?[ \t]*```[ \t]*\n?", text[end + 1:])
        if fence:
            end_ext += fence.end()
        ranges.append((m.start(), end_ext))
    cleaned = text
    for start, stop in sorted(ranges, key=lambda r: r[0], reverse=True):
        cleaned = cleaned[:start] + cleaned[stop:]
    return cleaned.strip(), cards


def _mrkdwn(s: str) -> str:
    """Light markdown → Slack mrkdwn: ** bold ** becomes * bold *."""
    return (s or "").replace("**", "*")


def _card_to_blocks(card: dict, counter: list) -> list:
    """Translate one card dict into Slack blocks. `counter` yields unique ids."""
    blocks = []
    if card.get("badge"):
        blocks.append({"type": "context",
                       "elements": [{"type": "mrkdwn", "text": _mrkdwn(str(card["badge"]))}]})

    section_lines = []
    if card.get("title"):
        section_lines.append(f"*{_mrkdwn(str(card['title']))}*")
    if card.get("subtitle"):
        section_lines.append(f"_{_mrkdwn(str(card['subtitle']))}_")
    for line in card.get("lines", []):
        section_lines.append(_mrkdwn(str(line)))
    if card.get("location"):
        section_lines.append(f"📍 {_mrkdwn(str(card['location']))}")

    section = {"type": "section",
               "text": {"type": "mrkdwn", "text": "\n".join(section_lines) or " "}}
    image = card.get("image")
    if isinstance(image, str) and image.startswith("http"):
        section["accessory"] = {"type": "image", "image_url": image,
                                "alt_text": str(card.get("title") or "image")}
    blocks.append(section)

    elements = []
    for act in card.get("actions", []):
        kind = act.get("kind")
        label = str(act.get("label") or "Select")[:75]
        counter[0] += 1
        button = {"type": "button", "text": {"type": "plain_text", "text": label, "emoji": True}}
        if kind == "link" and act.get("value"):
            button["url"] = str(act["value"])
            button["action_id"] = f"card_link_{counter[0]}"
            elements.append(button)
        elif kind == "message":
            # Carried back to us on click; the click handler runs the agent with it.
            button["value"] = str(act.get("value") or label)[:2000]
            button["action_id"] = f"card_msg_{counter[0]}"
            elements.append(button)
        # kind == "ics" and others are skipped — not expressible as a Slack button.

    if elements:
        # Slack allows max 25 elements per actions block.
        blocks.append({"type": "actions", "elements": elements[:25]})
    return blocks


def _build_reply(text: str):
    """Return (fallback_text, blocks_or_None) for a Slack message."""
    cleaned, cards = _extract_cards(text)
    if not cards:
        return text, None
    blocks = []
    if cleaned:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": _mrkdwn(cleaned)}})
    counter = [0]
    for card in cards:
        blocks.extend(_card_to_blocks(card, counter))
    # Slack caps a message at 50 blocks; keep the intro plus as many as fit.
    blocks = blocks[:50]
    fallback = cleaned or "(interactive message)"
    return fallback, blocks


async def _post_reply(bot_token: str, channel: str, text: str,
                      thread_ts: Optional[str], blocks: Optional[list] = None) -> None:
    payload = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    if thread_ts:
        payload["thread_ts"] = thread_ts
    headers = {"Authorization": f"Bearer {bot_token}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(SLACK_POST_MESSAGE_URL, json=payload, headers=headers)
        data = resp.json()
        if not data.get("ok"):
            logger.error("Slack chat.postMessage failed: %s", data.get("error"))


def _conversation_ids(team_id: str, channel: str, thread_ts: Optional[str], is_dm: bool):
    """Map a Slack location to (session_id, reply_thread), consistent across
    message events and button interactions."""
    if is_dm:
        # Direct message: one stable conversation per DM channel; reply inline.
        return f"slack_{team_id}_{channel}", None
    # Channel: keep the exchange in one thread so each thread is its own session.
    reply_thread = thread_ts
    return f"slack_{team_id}_{channel}_{reply_thread}", reply_thread


async def _dispatch(integration_dict: dict, channel: str, slack_user: str,
                    session_id: str, reply_thread: Optional[str], user_text: str) -> None:
    """Run the agent for `user_text` and post the (card-aware) reply to Slack."""
    if not channel or not user_text:
        return
    try:
        reply = await run_agent_message(
            agent_name=integration_dict["agent_name"],
            user_id=f"slack_{slack_user}",
            session_id=session_id,
            text=user_text,
        )
    except Exception as exc:
        logger.exception("Agent invocation failed for Slack dispatch: %s", exc)
        reply = "Sorry — I hit an error while processing that."

    if reply:
        fallback, blocks = _build_reply(reply)
        await _post_reply(integration_dict["bot_token"], channel, fallback, reply_thread, blocks)


async def _handle_event(integration_dict: dict, event: dict) -> None:
    """Background task for an inbound message event (mention or DM)."""
    channel = event.get("channel")
    user_text = _MENTION_RE.sub("", event.get("text", "")).strip()
    team_id = integration_dict.get("team_id") or "t"
    is_dm = event.get("channel_type") == "im"
    thread_ts = event.get("thread_ts") or event.get("ts")
    session_id, reply_thread = _conversation_ids(team_id, channel, thread_ts, is_dm)
    await _dispatch(integration_dict, channel, event.get("user") or "unknown",
                    session_id, reply_thread, user_text)


@router.post("/events")
async def slack_events(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=400)

    # URL verification handshake (no signature/team available yet).
    if payload.get("type") == "url_verification":
        return {"challenge": payload.get("challenge", "")}

    team_id = payload.get("team_id")
    integration = _get_integration(team_id)
    if not integration:
        logger.warning("No active Slack integration for team_id=%s", team_id)
        return Response(status_code=404)

    signature = request.headers.get("X-Slack-Signature", "")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    if not _verify_signature(integration.signing_secret, timestamp, raw_body, signature):
        logger.warning("Invalid Slack signature for team_id=%s", team_id)
        return Response(status_code=401)

    if payload.get("type") != "event_callback":
        return Response(status_code=200)

    # De-duplicate Slack's at-least-once retries.
    if _already_handled(payload.get("event_id")):
        return Response(status_code=200)

    event = payload.get("event") or {}
    # Accept channel mentions and direct messages to the bot.
    is_mention = event.get("type") == "app_mention"
    is_direct_message = event.get("type") == "message" and event.get("channel_type") == "im"
    if not (is_mention or is_direct_message):
        return Response(status_code=200)
    # Ignore the bot's own messages and edit/delete/system subtypes to avoid loops.
    if event.get("bot_id") or event.get("subtype"):
        return Response(status_code=200)

    # Snapshot the fields the background task needs (detached from the DB session).
    integration_dict = {
        "agent_name": integration.agent_name,
        "bot_token": integration.bot_token,
        "team_id": integration.team_id,
    }
    background_tasks.add_task(_handle_event, integration_dict, event)

    # Slack requires a response within 3s; ack now and process asynchronously.
    return Response(status_code=200)


@router.post("/interactions")
async def slack_interactions(request: Request, background_tasks: BackgroundTasks):
    """Handle Block Kit button clicks. A card 'message' button carries the text
    to send back to the agent as if the user had typed it."""
    raw_body = await request.body()
    parsed = parse_qs(raw_body.decode("utf-8", errors="replace"))
    payload_raw = (parsed.get("payload") or [""])[0]
    if not payload_raw:
        return Response(status_code=400)
    try:
        payload = json.loads(payload_raw)
    except json.JSONDecodeError:
        return Response(status_code=400)

    team_id = (payload.get("team") or {}).get("id")
    integration = _get_integration(team_id)
    if not integration:
        logger.warning("No active Slack integration for interaction team_id=%s", team_id)
        return Response(status_code=404)

    signature = request.headers.get("X-Slack-Signature", "")
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    if not _verify_signature(integration.signing_secret, timestamp, raw_body, signature):
        logger.warning("Invalid Slack signature for interaction team_id=%s", team_id)
        return Response(status_code=401)

    if payload.get("type") != "block_actions":
        return Response(status_code=200)

    actions = payload.get("actions") or []
    value = actions[0].get("value") if actions else None
    if not value:
        # Link buttons (url) carry no value — Slack opens the URL, nothing to run.
        return Response(status_code=200)

    # De-duplicate Slack's retries (each click has a unique action_ts).
    if _already_handled(actions[0].get("action_ts")):
        return Response(status_code=200)

    channel = (payload.get("channel") or {}).get("id")
    slack_user = (payload.get("user") or {}).get("id") or "unknown"
    message = payload.get("message") or {}
    is_dm = bool(channel and channel.startswith("D"))
    thread_ts = message.get("thread_ts") or message.get("ts")
    session_id, reply_thread = _conversation_ids(
        integration.team_id or "t", channel, thread_ts, is_dm)

    integration_dict = {
        "agent_name": integration.agent_name,
        "bot_token": integration.bot_token,
        "team_id": integration.team_id,
    }
    background_tasks.add_task(_dispatch, integration_dict, channel, slack_user,
                             session_id, reply_thread, value)

    # Ack within 3s; the agent runs asynchronously.
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# Dashboard CRUD for channel integrations
# ---------------------------------------------------------------------------

_EDITABLE_FIELDS = (
    "platform", "project_id", "agent_name", "label",
    "team_id", "bot_token", "signing_secret", "is_active",
)


@dashboard_router.get("/channel-integrations")
async def list_integrations(username: str = Depends(_dashboard_auth())):
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        rows = session.query(ChannelIntegration).order_by(
            ChannelIntegration.created_at.desc()
        ).all()
        return {"success": True, "integrations": [r.to_dict() for r in rows]}
    finally:
        session.close()


@dashboard_router.post("/channel-integrations")
async def create_integration(request: Request, username: str = Depends(_dashboard_auth())):
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    data = await request.json()
    project_id = data.get("project_id")
    agent_name = (data.get("agent_name") or "").strip()
    if not project_id or not agent_name:
        raise HTTPException(status_code=400, detail="project_id and agent_name are required")

    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        integration = ChannelIntegration(
            platform=(data.get("platform") or "slack").strip(),
            project_id=project_id,
            agent_name=agent_name,
            label=data.get("label"),
            team_id=(data.get("team_id") or None),
            bot_token=data.get("bot_token"),
            signing_secret=data.get("signing_secret"),
            is_active=data.get("is_active", True),
        )
        if isinstance(data.get("config"), dict):
            integration.set_config(data["config"])
        session.add(integration)
        session.commit()
        session.refresh(integration)
        return {"success": True, "integration": integration.to_dict()}
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        session.close()


@dashboard_router.put("/channel-integrations/{integration_id}")
async def update_integration(integration_id: int, request: Request,
                             username: str = Depends(_dashboard_auth())):
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    data = await request.json()
    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        integration = session.query(ChannelIntegration).filter_by(id=integration_id).first()
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")
        for field in _EDITABLE_FIELDS:
            if field in data:
                val = data[field]
                if field == "agent_name" and isinstance(val, str):
                    val = val.strip()
                setattr(integration, field, val)
        if isinstance(data.get("config"), dict):
            integration.set_config(data["config"])
        session.commit()
        session.refresh(integration)
        return {"success": True, "integration": integration.to_dict()}
    except HTTPException:
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        session.close()


@dashboard_router.delete("/channel-integrations/{integration_id}")
async def delete_integration(integration_id: int, username: str = Depends(_dashboard_auth())):
    if not username:
        raise HTTPException(status_code=401, detail="Not authenticated")
    db = get_database_client()
    session = db.get_session()
    if not session:
        raise HTTPException(status_code=500, detail="Database unavailable")
    try:
        integration = session.query(ChannelIntegration).filter_by(id=integration_id).first()
        if not integration:
            raise HTTPException(status_code=404, detail="Integration not found")
        session.delete(integration)
        session.commit()
        return {"success": True}
    except HTTPException:
        raise
    except Exception as exc:
        session.rollback()
        raise HTTPException(status_code=500, detail=str(exc))
    finally:
        session.close()
