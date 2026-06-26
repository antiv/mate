"""Native Google Calendar tools (service-account auth, mirrors google_drive_tools).

Exposes function tools to an agent: current date/time, check availability (free/busy), list /
create / cancel / reschedule events — on a configured calendar reached via a Google **service
account** (`GOOGLE_SERVICE_ACCOUNT_INFO`). For production the customer shares their calendar with
the service account's email and the agent's tool config points at that calendar id. For wizard
trials no customer credentials are needed — a demo calendar (`WIZARD_DEMO_CALENDAR_ID`) is used.

tool_config shape:
    "google_calendar": true
    "google_calendar": {
        "calendar_id": "abc@group.calendar.google.com",
        "timezone": "Europe/Belgrade",
        "working_hours": "Mon-Fri 09:00-17:00, Sat 09:00-13:00",
        "slot_minutes": 30
    }
"""

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from google.adk.tools.tool_context import ToolContext

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/calendar"]


def get_google_calendar_service():
    """Authenticated Google Calendar v3 service via Service Account (same pattern as Drive)."""
    from google.oauth2 import service_account
    from google.auth import default
    from googleapiclient.discovery import build

    service_account_info = os.getenv("GOOGLE_SERVICE_ACCOUNT_INFO")
    if service_account_info:
        credentials = service_account.Credentials.from_service_account_info(
            json.loads(service_account_info), scopes=SCOPES
        )
    else:
        service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "service-account-key.json")
        if os.path.exists(service_account_file):
            credentials = service_account.Credentials.from_service_account_file(
                service_account_file, scopes=SCOPES
            )
        else:
            credentials, _ = default(scopes=SCOPES)
    return build("calendar", "v3", credentials=credentials)


def _resolve_calendar_id(cfg_calendar_id: Optional[str]) -> str:
    # No silent fallback to the service account's own (invisible) "primary" calendar —
    # require an explicit calendar so the agent can't appear to "work" against an empty calendar.
    return (cfg_calendar_id or os.getenv("WIZARD_DEMO_CALENDAR_ID") or "").strip()


def _to_rfc3339(value: str, tz_name: str) -> str:
    """Ensure an ISO datetime is timezone-aware RFC 3339 (attach the calendar's tz if naive).

    The Google free/busy and events.list APIs require an offset/Z; the LLM may pass a naive
    datetime, so we attach the configured timezone's offset for that date.
    """
    if not value:
        return value
    s = str(value).strip()
    if s.endswith("Z") or re.search(r"[+-]\d{2}:?\d{2}$", s):
        return s
    try:
        from zoneinfo import ZoneInfo
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        return dt.isoformat()
    except Exception:
        return s


def _valid_phone(phone: str) -> bool:
    """A phone number is valid if, after stripping common separators, it is 8-15 digits with an
    optional leading '+'. Covers local (0641234567) and international (+381641234567) formats."""
    digits = re.sub(r"[\s\-().]", "", str(phone or ""))
    return bool(re.fullmatch(r"\+?\d{8,15}", digits))


def _to_local(value: str, tz_name: str) -> str:
    """Convert any ISO datetime (incl. UTC 'Z' from Google) to the calendar's local timezone,
    so the agent always reasons about local clock times (avoids UTC/local confusion)."""
    if not value:
        return value
    try:
        from zoneinfo import ZoneInfo
        s = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(tz_name))
        return dt.astimezone(ZoneInfo(tz_name)).isoformat()
    except Exception:
        return value


# --- Demo mode --------------------------------------------------------------
# Wizard trials have no real Google Calendar configured. When tool_config sets
# "google_calendar": {"demo": true}, the tools simulate a working calendar (a seeded daily
# busy block + in-memory bookings) so a prospect experiences the full flow as if it were
# connected. State is per-agent and in-process (resets on restart) — that is fine for a demo.
_DEMO_STORE: Dict[str, List[Dict[str, Any]]] = {}
_DEMO_BUSY_HOUR = (13, 14)  # seeded daily "lunch" busy block (local time), to make availability realistic


def _parse_local(value: str, tz_name: str):
    """Parse an ISO datetime into an aware datetime in the calendar's local timezone."""
    from zoneinfo import ZoneInfo
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz_name))
    return dt.astimezone(ZoneInfo(tz_name))


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    return a_start < b_end and b_start < a_end


def _demo_seed_busy(win_start, win_end, tz_name):
    """Seeded busy blocks (a daily lunch hour) overlapping the requested window."""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(tz_name)
    out, day = [], win_start.date()
    while day <= win_end.date():
        s = datetime(day.year, day.month, day.day, _DEMO_BUSY_HOUR[0], 0, tzinfo=tz)
        e = datetime(day.year, day.month, day.day, _DEMO_BUSY_HOUR[1], 0, tzinfo=tz)
        if _overlaps(win_start, win_end, s, e):
            out.append((s, e))
        day += timedelta(days=1)
    return out


def _demo_check_availability(key, start_iso, end_iso, tz_name):
    ws, we = _parse_local(start_iso, tz_name), _parse_local(end_iso, tz_name)
    if not ws or not we:
        return {"status": "error", "message": "Invalid start/end datetime."}
    busy = [(s, e) for (s, e) in _demo_seed_busy(ws, we, tz_name)]
    for ev in _DEMO_STORE.get(key, []):
        s, e = _parse_local(ev["start"], tz_name), _parse_local(ev["end"], tz_name)
        if s and e and _overlaps(ws, we, s, e):
            busy.append((s, e))
    busy.sort()
    return {"status": "success", "calendar_id": "demo", "timezone": tz_name, "demo": True,
            "window": {"start": ws.isoformat(), "end": we.isoformat()}, "is_free": not busy,
            "busy": [{"start": s.isoformat(), "end": e.isoformat()} for s, e in busy]}


def _demo_list(key, start_iso, end_iso, tz_name):
    ws, we = _parse_local(start_iso, tz_name), _parse_local(end_iso, tz_name)
    evs = []
    for ev in _DEMO_STORE.get(key, []):
        s, e = _parse_local(ev["start"], tz_name), _parse_local(ev["end"], tz_name)
        if ws and we and s and e and _overlaps(ws, we, s, e):
            evs.append({"id": ev["id"], "summary": ev["summary"], "start": s.isoformat(), "end": e.isoformat()})
    evs.sort(key=lambda x: x["start"])
    return {"status": "success", "calendar_id": "demo", "events": evs, "demo": True}


def _demo_create(key, summary, start_iso, end_iso, description, phone, tz_name):
    s, e = _parse_local(start_iso, tz_name), _parse_local(end_iso, tz_name)
    eid = "demo-" + uuid.uuid4().hex[:10]
    link = "https://calendar.google.com/calendar/u/0/r"
    ev = {"id": eid, "summary": summary, "description": description or "", "phone": phone,
          "start": s.isoformat() if s else start_iso, "end": e.isoformat() if e else end_iso, "html_link": link}
    _DEMO_STORE.setdefault(key, []).append(ev)
    card = "[[APPOINTMENT]]" + json.dumps(
        {"summary": summary, "start": ev["start"], "end": ev["end"], "html_link": link}, ensure_ascii=False)
    return {"status": "success", "event_id": eid, "html_link": link, "summary": summary,
            "start": ev["start"], "end": ev["end"], "card": card, "demo": True}


def _demo_cancel(key, event_id):
    lst = _DEMO_STORE.get(key, [])
    for i, ev in enumerate(lst):
        if ev["id"] == event_id:
            lst.pop(i)
            return {"status": "success", "event_id": event_id, "message": "Appointment cancelled.", "demo": True}
    return {"status": "error", "message": f"No appointment with id {event_id} was found.", "demo": True}


def _demo_reschedule(key, event_id, start_iso, end_iso, tz_name):
    s, e = _parse_local(start_iso, tz_name), _parse_local(end_iso, tz_name)
    for ev in _DEMO_STORE.get(key, []):
        if ev["id"] == event_id:
            ev["start"], ev["end"] = (s.isoformat() if s else start_iso), (e.isoformat() if e else end_iso)
            return {"status": "success", "event_id": event_id, "html_link": ev.get("html_link"),
                    "start": ev["start"], "end": ev["end"], "message": "Appointment rescheduled.", "demo": True}
    return {"status": "error", "message": f"No appointment with id {event_id} was found.", "demo": True}


_NOT_CONFIGURED = {
    "status": "error",
    "message": (
        "No calendar is configured for this agent, so availability and bookings cannot be made. "
        "Set a Calendar ID in the agent's tool config (and share that calendar with the service "
        "account email), or set WIZARD_DEMO_CALENDAR_ID."
    ),
}


def create_google_calendar_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """Build the calendar tools if enabled, bound to the configured calendar + scheduling rules."""
    tool_config = config.get("tool_config")
    if isinstance(tool_config, str):
        try:
            tool_config = json.loads(tool_config)
        except json.JSONDecodeError:
            tool_config = {}
    tool_config = tool_config or {}

    cal_cfg = tool_config.get("google_calendar")
    if not cal_cfg:
        return []

    cfg = cal_cfg if isinstance(cal_cfg, dict) else {}
    demo = bool(cfg.get("demo"))
    demo_key = config.get("name") or "demo"
    calendar_id = _resolve_calendar_id(cfg.get("calendar_id"))
    timezone = cfg.get("timezone") or os.getenv("WIZARD_CALENDAR_TIMEZONE", "Europe/Belgrade")
    working_hours = (cfg.get("working_hours") or "").strip() or None
    try:
        slot_minutes = int(cfg.get("slot_minutes") or os.getenv("WIZARD_DEFAULT_SLOT_MIN", "30"))
    except (TypeError, ValueError):
        slot_minutes = 30

    def get_current_datetime(tool_context: ToolContext = None) -> Dict[str, Any]:
        """Get the current date, time, weekday and the business scheduling rules.

        Call this FIRST for any scheduling request: it resolves relative dates ('today',
        'tomorrow', 'this Friday') AND tells you the business working hours and the default
        appointment length. Only offer times within working_hours, using slot_minutes as the
        appointment duration unless the visitor asks otherwise.

        Returns:
            Dict with now_iso, date, time, weekday, timezone, working_hours, slot_minutes.
        """
        try:
            from zoneinfo import ZoneInfo
            now = datetime.now(ZoneInfo(timezone))
        except Exception:
            now = datetime.now()
        return {
            "status": "success",
            "now_iso": now.isoformat(),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "weekday": now.strftime("%A"),
            "timezone": timezone,
            "working_hours": working_hours or "not specified",
            "slot_minutes": slot_minutes,
        }

    def check_availability(start_iso: str, end_iso: str, tool_context: ToolContext = None) -> Dict[str, Any]:
        """Check whether the calendar is free between two ISO 8601 datetimes.

        Args:
            start_iso: Start of the window, e.g. '2026-07-01T09:00:00' (or with offset).
            end_iso: End of the window.

        Returns:
            Dict with 'is_free' (bool) and 'busy' (list of busy {start,end} intervals).
        """
        if demo:
            return _demo_check_availability(demo_key, start_iso, end_iso, timezone)
        if not calendar_id:
            return dict(_NOT_CONFIGURED)
        try:
            service = get_google_calendar_service()
            body = {
                "timeMin": _to_rfc3339(start_iso, timezone),
                "timeMax": _to_rfc3339(end_iso, timezone),
                "items": [{"id": calendar_id}],
            }
            result = service.freebusy().query(body=body).execute()
            busy = result.get("calendars", {}).get(calendar_id, {}).get("busy", [])
            busy_local = [{"start": _to_local(b.get("start"), timezone), "end": _to_local(b.get("end"), timezone)} for b in busy]
            return {"status": "success", "calendar_id": calendar_id, "timezone": timezone,
                    "window": {"start": _to_local(body["timeMin"], timezone), "end": _to_local(body["timeMax"], timezone)},
                    "is_free": len(busy) == 0, "busy": busy_local}
        except Exception as exc:
            logger.warning("check_availability failed: %s", exc)
            return {"status": "error", "message": f"Could not check availability: {exc}"}

    def list_events(start_iso: str, end_iso: str, max_results: int = 10,
                    tool_context: ToolContext = None) -> Dict[str, Any]:
        """List events between two ISO 8601 datetimes (use to find an event id to cancel/reschedule).

        Returns:
            Dict with 'events' (list of {id, summary, start, end}).
        """
        if demo:
            return _demo_list(demo_key, start_iso, end_iso, timezone)
        if not calendar_id:
            return dict(_NOT_CONFIGURED)
        try:
            service = get_google_calendar_service()
            result = service.events().list(
                calendarId=calendar_id, timeMin=_to_rfc3339(start_iso, timezone),
                timeMax=_to_rfc3339(end_iso, timezone), maxResults=max_results,
                singleEvents=True, orderBy="startTime",
            ).execute()
            events = [
                {
                    "id": e.get("id"),
                    "summary": e.get("summary", "(no title)"),
                    "start": _to_local(e.get("start", {}).get("dateTime") or e.get("start", {}).get("date"), timezone),
                    "end": _to_local(e.get("end", {}).get("dateTime") or e.get("end", {}).get("date"), timezone),
                }
                for e in result.get("items", [])
            ]
            return {"status": "success", "calendar_id": calendar_id, "events": events}
        except Exception as exc:
            logger.warning("list_events failed: %s", exc)
            return {"status": "error", "message": f"Could not list events: {exc}"}

    def create_event(summary: str, start_iso: str, end_iso: str, description: str = "",
                     phone: str = "", tool_context: ToolContext = None) -> Dict[str, Any]:
        """Create an appointment/event on the calendar.

        Args:
            summary: Event title (e.g. 'Haircut - Pera').
            start_iso: Start datetime (ISO 8601).
            end_iso: End datetime (ISO 8601).
            description: Optional notes (include the visitor's name and reason).
            phone: Visitor's contact phone number. If provided it MUST be a valid format
                (8-15 digits, optional leading '+'); an invalid number is rejected.

        Returns:
            On success: dict with the created event id, html_link, times and a ready 'card'.
            On invalid phone: dict with status 'error' and error 'invalid_phone' — ask the
            visitor for a valid phone number and retry.
        """
        if phone and not _valid_phone(phone):
            return {
                "status": "error", "error": "invalid_phone",
                "message": (f"The phone number '{phone}' is not a valid format. Ask the visitor for a "
                            "valid phone number (8-15 digits, optionally starting with + or 0) and try again."),
            }
        if demo:
            return _demo_create(demo_key, summary, start_iso, end_iso, description, phone, timezone)
        if not calendar_id:
            return dict(_NOT_CONFIGURED)
        try:
            service = get_google_calendar_service()
            full_description = (description or "").strip()
            if phone:
                full_description = (full_description + f"\nPhone: {phone}").strip()
            event_body = {
                "summary": summary,
                "description": full_description,
                "start": {"dateTime": _to_rfc3339(start_iso, timezone), "timeZone": timezone},
                "end": {"dateTime": _to_rfc3339(end_iso, timezone), "timeZone": timezone},
            }
            created = service.events().insert(calendarId=calendar_id, body=event_body).execute()
            card = "[[APPOINTMENT]]" + json.dumps(
                {"summary": summary, "start": start_iso, "end": end_iso, "html_link": created.get("htmlLink")},
                ensure_ascii=False)
            return {
                "status": "success", "event_id": created.get("id"), "html_link": created.get("htmlLink"),
                "summary": summary, "start": start_iso, "end": end_iso,
                "card": card,
            }
        except Exception as exc:
            logger.warning("create_event failed: %s", exc)
            return {"status": "error", "message": f"Could not create event: {exc}"}

    def cancel_event(event_id: str, tool_context: ToolContext = None) -> Dict[str, Any]:
        """Cancel (delete) an appointment by its event id (get the id from list_events)."""
        if not event_id:
            return {"status": "error", "message": "event_id is required (use list_events to find it)."}
        if demo:
            return _demo_cancel(demo_key, event_id)
        if not calendar_id:
            return dict(_NOT_CONFIGURED)
        try:
            service = get_google_calendar_service()
            service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
            return {"status": "success", "event_id": event_id, "message": "Appointment cancelled."}
        except Exception as exc:
            logger.warning("cancel_event failed: %s", exc)
            return {"status": "error", "message": f"Could not cancel the appointment: {exc}"}

    def reschedule_event(event_id: str, start_iso: str, end_iso: str,
                         tool_context: ToolContext = None) -> Dict[str, Any]:
        """Move an appointment to a new time (get the id from list_events)."""
        if not event_id:
            return {"status": "error", "message": "event_id is required (use list_events to find it)."}
        if demo:
            return _demo_reschedule(demo_key, event_id, start_iso, end_iso, timezone)
        if not calendar_id:
            return dict(_NOT_CONFIGURED)
        try:
            service = get_google_calendar_service()
            patched = service.events().patch(calendarId=calendar_id, eventId=event_id, body={
                "start": {"dateTime": _to_rfc3339(start_iso, timezone), "timeZone": timezone},
                "end": {"dateTime": _to_rfc3339(end_iso, timezone), "timeZone": timezone},
            }).execute()
            return {
                "status": "success", "event_id": event_id, "html_link": patched.get("htmlLink"),
                "start": start_iso, "end": end_iso, "message": "Appointment rescheduled.",
            }
        except Exception as exc:
            logger.warning("reschedule_event failed: %s", exc)
            return {"status": "error", "message": f"Could not reschedule the appointment: {exc}"}

    logger.info("Registered Google Calendar tools for agent '%s' (calendar_id=%s)",
                config.get("name", "unknown"), calendar_id)
    return [get_current_datetime, check_availability, list_events, create_event, cancel_event, reschedule_event]
