#!/usr/bin/env python3
"""
Trigger delivery must keep raising when it fails.

The HTTP and email senders moved into shared/utils/notify.py, where they report
failure as a flag instead of an exception. _execute_trigger_sync records a
trigger's outcome from the raised exception, so if the wrappers swallowed that
flag every failed delivery would be recorded as a success.
"""

import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.trigger_runner import TriggerRunner


class TestTriggerHttpOutput(unittest.TestCase):

    def setUp(self):
        self.runner = TriggerRunner()

    def test_payload_shape_is_unchanged(self):
        with patch("shared.utils.trigger_runner.post_json", return_value=(True, "HTTP 200")) as post:
            self.runner._output_http_callback({"url": "https://x", "timeout": 5}, "hello")
        self.assertEqual(post.call_args.args[0], "https://x")
        self.assertEqual(post.call_args.kwargs["payload"],
                         {"response": "hello", "source": "mate_trigger"})
        self.assertEqual(post.call_args.kwargs["timeout"], 5.0)

    def test_delivery_failure_raises(self):
        with patch("shared.utils.trigger_runner.post_json", return_value=(False, "ConnectError")):
            with self.assertRaises(RuntimeError):
                self.runner._output_http_callback({"url": "https://x"}, "hello")

    def test_missing_url_is_a_no_op_not_an_error(self):
        with patch("shared.utils.trigger_runner.post_json") as post:
            self.runner._output_http_callback({}, "hello")
        post.assert_not_called()


class TestTriggerEmailOutput(unittest.TestCase):

    def setUp(self):
        self.runner = TriggerRunner()

    def test_sends_with_the_configured_subject(self):
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            with patch("shared.utils.trigger_runner.send_email", return_value=(True, "sent")) as send:
                self.runner._output_email({"to": "a@b.c", "subject": "Result"}, "body")
        self.assertEqual(send.call_args.args, ("a@b.c", "Result", "body"))

    def test_delivery_failure_raises(self):
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            with patch("shared.utils.trigger_runner.send_email", return_value=(False, "SMTPAuthenticationError")):
                with self.assertRaises(RuntimeError):
                    self.runner._output_email({"to": "a@b.c"}, "body")

    def test_misconfiguration_is_a_no_op_not_an_error(self):
        with patch.dict(os.environ, {"SMTP_HOST": ""}):
            with patch("shared.utils.trigger_runner.send_email") as send:
                self.runner._output_email({"to": "a@b.c"}, "body")
        send.assert_not_called()


class TestNotifyPrimitives(unittest.TestCase):

    def test_post_json_reports_failure_instead_of_raising(self):
        from shared.utils.notify import post_json
        with patch("httpx.Client") as client:
            client.return_value.__enter__.return_value.post.side_effect = RuntimeError("refused")
            ok, detail = post_json("https://x", {"a": 1})
        self.assertFalse(ok)
        self.assertIn("refused", detail)

    def test_post_json_rejects_a_missing_url(self):
        from shared.utils.notify import post_json
        self.assertEqual(post_json("", {})[0], False)

    def test_send_email_without_smtp_host_reports_failure(self):
        from shared.utils.notify import send_email
        with patch.dict(os.environ, {"SMTP_HOST": ""}):
            ok, detail = send_email("a@b.c", "s", "b")
        self.assertFalse(ok)
        self.assertIn("SMTP_HOST", detail)

    def test_send_email_applies_a_socket_timeout(self):
        # Without this a dead SMTP host hangs an APScheduler worker indefinitely
        from shared.utils.notify import send_email
        with patch.dict(os.environ, {"SMTP_HOST": "smtp.example.com"}):
            with patch("smtplib.SMTP") as smtp:
                send_email("a@b.c", "s", "b", timeout=7)
        self.assertEqual(smtp.call_args.kwargs.get("timeout"), 7)


class TestOldBudgetAlerterIsGone(unittest.TestCase):

    def test_callback_module_no_longer_alerts_inline(self):
        import shared.callbacks.token_usage_callback as cb
        # Both the process-local dedupe dict and the inline sender are gone; the
        # after-model callback must not do network I/O.
        self.assertFalse(hasattr(cb, "_budget_alert_sent"))
        self.assertFalse(hasattr(cb, "_maybe_fire_budget_alerts"))

    def test_rate_limit_service_webhook_senders_removed(self):
        from shared.utils.rate_limit_service import RateLimitService
        self.assertFalse(hasattr(RateLimitService, "send_alert_webhook_sync"))
        self.assertFalse(hasattr(RateLimitService, "send_alert_webhook"))


if __name__ == '__main__':
    unittest.main()
