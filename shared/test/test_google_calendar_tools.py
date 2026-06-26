#!/usr/bin/env python3
"""Unit tests for the native Google Calendar tools (mocked Google service)."""

import unittest
import re
from unittest.mock import MagicMock, patch
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shared.utils.tools.google_calendar_tools as gcal


def _resp(content):
    m = MagicMock()
    m.choices = [MagicMock()]
    m.choices[0].message.content = content
    return m


def _tools(tool_config='{"google_calendar": {"calendar_id": "primary"}}'):
    return gcal.create_google_calendar_tools_from_config({"name": "a", "tool_config": tool_config})


def _by_name(tool_config='{"google_calendar": {"calendar_id": "primary"}}'):
    return {f.__name__: f for f in _tools(tool_config)}


class TestGoogleCalendarTools(unittest.TestCase):

    def test_from_config_gating(self):
        self.assertEqual(_tools('{"browser": true}'), [])
        names = sorted(_by_name('{"google_calendar": true}').keys())
        self.assertEqual(names, ["cancel_event", "check_availability", "create_event",
                                 "get_current_datetime", "list_events", "reschedule_event"])

    @patch.object(gcal, "get_google_calendar_service")
    def test_check_availability_free(self, mock_service):
        svc = MagicMock()
        svc.freebusy.return_value.query.return_value.execute.return_value = {"calendars": {"primary": {"busy": []}}}
        mock_service.return_value = svc
        res = _by_name()["check_availability"]("2026-07-01T09:00:00+02:00", "2026-07-01T17:00:00+02:00")
        self.assertEqual(res["status"], "success")
        self.assertTrue(res["is_free"])

    @patch.object(gcal, "get_google_calendar_service")
    def test_check_availability_normalizes_naive_datetime(self, mock_service):
        svc = MagicMock()
        svc.freebusy.return_value.query.return_value.execute.return_value = {"calendars": {"primary": {"busy": []}}}
        mock_service.return_value = svc
        _by_name()["check_availability"]("2026-07-01T09:00:00", "2026-07-01T17:00:00")  # naive
        body = svc.freebusy.return_value.query.call_args.kwargs["body"]
        self.assertRegex(body["timeMin"], r"[+-]\d{2}:\d{2}$")  # offset attached

    @patch.object(gcal, "get_google_calendar_service")
    def test_create_and_list_and_cancel_and_reschedule(self, mock_service):
        svc = MagicMock()
        svc.events.return_value.insert.return_value.execute.return_value = {"id": "evt1", "htmlLink": "http://c/evt1"}
        svc.events.return_value.list.return_value.execute.return_value = {"items": [
            {"id": "evt1", "summary": "X", "start": {"dateTime": "2026-07-01T10:00:00Z"}, "end": {"dateTime": "2026-07-01T10:30:00Z"}}]}
        svc.events.return_value.delete.return_value.execute.return_value = {}
        svc.events.return_value.patch.return_value.execute.return_value = {"htmlLink": "http://c/evt1b"}
        mock_service.return_value = svc
        t = _by_name()

        ev = t["create_event"]("X", "2026-07-01T10:00:00", "2026-07-01T10:30:00")
        self.assertEqual(ev["event_id"], "evt1")
        self.assertTrue(ev["card"].startswith("[[APPOINTMENT]]"))  # ready-to-print card
        listed = t["list_events"]("2026-07-01T00:00:00", "2026-07-02T00:00:00")
        self.assertEqual(listed["events"][0]["id"], "evt1")  # id exposed for cancel/reschedule
        self.assertRegex(listed["events"][0]["start"], r"[+-]\d{2}:\d{2}$")  # returned in local tz
        self.assertEqual(t["cancel_event"]("evt1")["status"], "success")
        self.assertEqual(t["reschedule_event"]("evt1", "2026-07-01T12:00:00", "2026-07-01T12:30:00")["status"], "success")

    @patch.object(gcal, "get_google_calendar_service")
    def test_create_event_rejects_invalid_phone(self, mock_service):
        svc = MagicMock()
        svc.events.return_value.insert.return_value.execute.return_value = {"id": "e", "htmlLink": "http://c/e"}
        mock_service.return_value = svc
        create = _by_name()["create_event"]
        bad = create("X", "2026-07-01T10:00:00", "2026-07-01T10:30:00", "n", "12")  # too short
        self.assertEqual(bad["status"], "error")
        self.assertEqual(bad["error"], "invalid_phone")
        svc.events.return_value.insert.assert_not_called()  # never booked with a bad phone
        ok = create("X", "2026-07-01T10:00:00", "2026-07-01T10:30:00", "n", "+381 64 123-4567")
        self.assertEqual(ok["status"], "success")

    def test_valid_phone(self):
        for good in ["+381641234567", "0641234567", "064 123 4567", "+1 (212) 555-1234"]:
            self.assertTrue(gcal._valid_phone(good), good)
        for bad in ["", "12", "abc", "123-456", "+", "phone please"]:
            self.assertFalse(gcal._valid_phone(bad), bad)

    def test_cancel_requires_event_id(self):
        res = _by_name()["cancel_event"]("")
        self.assertEqual(res["status"], "error")
        self.assertIn("event_id", res["message"])

    @patch.object(gcal, "get_google_calendar_service", side_effect=Exception("no creds"))
    def test_error_when_service_fails(self, _mock):
        res = _by_name()["check_availability"]("a", "b")
        self.assertEqual(res["status"], "error")

    @patch.dict(os.environ, {}, clear=False)
    def test_no_calendar_configured_returns_error(self):
        os.environ.pop("WIZARD_DEMO_CALENDAR_ID", None)
        t = {f.__name__: f for f in gcal.create_google_calendar_tools_from_config(
            {"name": "a", "tool_config": '{"google_calendar": true}'})}
        for name, args in [("check_availability", ("a", "b")), ("list_events", ("a", "b")),
                           ("create_event", ("s", "a", "b")), ("cancel_event", ("id",)),
                           ("reschedule_event", ("id", "a", "b"))]:
            res = t[name](*args)
            self.assertEqual(res["status"], "error", name)
            self.assertIn("no calendar is configured", res["message"].lower())

    def test_get_current_datetime_includes_rules(self):
        from datetime import datetime
        t = _by_name('{"google_calendar": {"working_hours": "Mon-Fri 9-17", "slot_minutes": 45}}')
        res = t["get_current_datetime"]()
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["date"][:4], str(datetime.now().year))
        self.assertEqual(res["working_hours"], "Mon-Fri 9-17")
        self.assertEqual(res["slot_minutes"], 45)


class TestDemoMode(unittest.TestCase):
    """Demo mode simulates a working calendar (no Google call) for wizard trials."""

    def setUp(self):
        gcal._DEMO_STORE.clear()

    def _demo(self):
        return {f.__name__: f for f in gcal.create_google_calendar_tools_from_config(
            {"name": "demoagent", "tool_config": '{"google_calendar": {"demo": true, "timezone": "Europe/Belgrade"}}'})}

    def test_demo_simulates_availability_without_google(self):
        t = self._demo()  # no service mock — must not call Google
        self.assertTrue(t["check_availability"]("2026-07-01T10:00:00", "2026-07-01T10:30:00")["is_free"])
        lunch = t["check_availability"]("2026-07-01T13:00:00", "2026-07-01T13:30:00")  # seeded busy block
        self.assertFalse(lunch["is_free"])
        self.assertTrue(lunch["demo"])

    def test_demo_book_makes_slot_busy_then_list_and_cancel(self):
        t = self._demo()
        c = t["create_event"]("AI consult - Ivan", "2026-07-01T10:00:00", "2026-07-01T10:30:00", "note", "0641234567")
        self.assertEqual(c["status"], "success")
        self.assertTrue(c["card"].startswith("[[APPOINTMENT]]"))
        eid = c["event_id"]
        self.assertFalse(t["check_availability"]("2026-07-01T10:00:00", "2026-07-01T10:30:00")["is_free"])  # now booked
        listed = t["list_events"]("2026-07-01T00:00:00", "2026-07-02T00:00:00")
        self.assertIn(eid, [e["id"] for e in listed["events"]])
        self.assertEqual(t["cancel_event"](eid)["status"], "success")
        self.assertNotIn(eid, [e["id"] for e in t["list_events"]("2026-07-01T00:00:00", "2026-07-02T00:00:00")["events"]])

    def test_demo_still_validates_phone(self):
        self.assertEqual(self._demo()["create_event"](
            "X", "2026-07-01T10:00:00", "2026-07-01T10:30:00", "n", "12")["error"], "invalid_phone")


class TestTzNormalization(unittest.TestCase):
    def test_naive_gets_offset(self):
        out = gcal._to_rfc3339("2026-07-01T10:00:00", "Europe/Belgrade")
        self.assertRegex(out, r"[+-]\d{2}:\d{2}$")  # CEST +02:00 in July

    def test_passthrough_when_aware(self):
        self.assertEqual(gcal._to_rfc3339("2026-07-01T10:00:00Z", "Europe/Belgrade"), "2026-07-01T10:00:00Z")
        self.assertEqual(gcal._to_rfc3339("2026-07-01T10:00:00+01:00", "Europe/Belgrade"), "2026-07-01T10:00:00+01:00")

    def test_to_local_converts_utc(self):
        # 10:00 UTC -> 12:00 in Belgrade (CEST +02:00) in July
        self.assertEqual(gcal._to_local("2026-07-01T10:00:00Z", "Europe/Belgrade"), "2026-07-01T12:00:00+02:00")


if __name__ == "__main__":
    unittest.main()
