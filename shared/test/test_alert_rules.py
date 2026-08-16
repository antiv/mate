#!/usr/bin/env python3
"""
Unit tests for the alert rule engine.

The cooldown is the part that matters most: the implementation this replaces
deduped through a process-local dict that never reset per period and was lost on
restart, so a broken agent could spam an endpoint forever after a redeploy.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.models import (
    AgentConfig, AlertRule, Base, GuardrailLog, RateLimitConfig,
)
from shared.utils.alert_service import AlertService


class _AlertTestCase(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.now = datetime.now(timezone.utc)

        self.service = AlertService()
        self.service._get_session = lambda: self.Session()

        # The measurement helpers reach for their own singletons
        self.token_service = MagicMock()
        self.guardrail_service = MagicMock()
        self.patchers = [
            patch("shared.utils.token_usage_service.get_token_usage_service",
                  return_value=self.token_service),
            patch("shared.utils.guardrail_log_service.get_guardrail_log_service",
                  return_value=self.guardrail_service),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        self.engine.dispose()

    def _rule(self, **kwargs):
        defaults = dict(name="rule", scope="agent", scope_id="a1",
                        condition_type="agent_error_count", destination_type="http",
                        cooldown_seconds=3600, is_enabled=True, fire_count=0)
        defaults.update(kwargs)
        condition = defaults.pop("condition_config", {"threshold": 3, "window_minutes": 15})
        destination = defaults.pop("destination_config", {"url": "https://hook.example/x"})
        session = self.Session()
        rule = AlertRule(**defaults)
        rule.set_condition_config(condition)
        rule.set_destination_config(destination)
        session.add(rule)
        session.commit()
        rule_id = rule.id
        session.close()
        return rule_id


class TestModel(_AlertTestCase):

    def test_json_config_round_trip(self):
        rule_id = self._rule(condition_config={"threshold": 7, "window_minutes": 30})
        session = self.Session()
        rule = session.get(AlertRule, rule_id)
        self.assertEqual(rule.get_condition_config()["threshold"], 7)
        self.assertEqual(rule.to_dict()["condition_config"]["window_minutes"], 30)
        session.close()

    def test_to_dict_redacts_destination_headers(self):
        rule_id = self._rule(destination_config={"url": "https://x", "headers": {"Authorization": "secret"}})
        session = self.Session()
        rule = session.get(AlertRule, rule_id)
        self.assertEqual(rule.to_dict()["destination_config"]["headers"], "***")
        self.assertEqual(rule.to_dict(include_secrets=True)["destination_config"]["headers"],
                         {"Authorization": "secret"})
        session.close()

    def test_malformed_json_does_not_raise(self):
        rule_id = self._rule()
        session = self.Session()
        rule = session.get(AlertRule, rule_id)
        rule.condition_config = "{not json"
        session.commit()
        self.assertEqual(rule.get_condition_config(), {})
        session.close()


class TestCooldown(_AlertTestCase):

    def test_fires_when_threshold_crossed(self):
        self.token_service.get_error_count_since.return_value = 5
        rule_id = self._rule()
        with patch("shared.utils.alert_service.post_json", return_value=(True, "HTTP 200")) as post:
            fired = self.service.evaluate_all()
        self.assertEqual([f["rule_id"] for f in fired], [rule_id])
        self.assertEqual(post.call_count, 1)

    def test_does_not_fire_below_threshold(self):
        self.token_service.get_error_count_since.return_value = 2
        self._rule()
        with patch("shared.utils.alert_service.post_json", return_value=(True, "ok")) as post:
            self.assertEqual(self.service.evaluate_all(), [])
        post.assert_not_called()

    def test_cooldown_suppresses_the_second_pass(self):
        self.token_service.get_error_count_since.return_value = 5
        self._rule()
        with patch("shared.utils.alert_service.post_json", return_value=(True, "ok")) as post:
            self.service.evaluate_all()
            self.service.evaluate_all()
        self.assertEqual(post.call_count, 1)

    def test_cooldown_is_read_from_the_database_not_memory(self):
        # A fresh service stands in for a restarted process: the suppression must hold
        self.token_service.get_error_count_since.return_value = 5
        rule_id = self._rule()
        with patch("shared.utils.alert_service.post_json", return_value=(True, "ok")) as post:
            self.service.evaluate_all()
            restarted = AlertService()
            restarted._get_session = lambda: self.Session()
            restarted.evaluate_all()
        self.assertEqual(post.call_count, 1)
        session = self.Session()
        self.assertEqual(session.get(AlertRule, rule_id).fire_count, 1)
        session.close()

    def test_fires_again_once_the_cooldown_expires(self):
        self.token_service.get_error_count_since.return_value = 5
        rule_id = self._rule(cooldown_seconds=60)
        with patch("shared.utils.alert_service.post_json", return_value=(True, "ok")) as post:
            self.service.evaluate_all()
            session = self.Session()
            rule = session.get(AlertRule, rule_id)
            rule.last_fired_at = self.now - timedelta(seconds=120)
            session.commit()
            session.close()
            self.service.evaluate_all()
        self.assertEqual(post.call_count, 2)

    def test_claim_lets_only_one_caller_through(self):
        # Two processes evaluating the same rule must not both deliver
        self.token_service.get_error_count_since.return_value = 5
        rule_id = self._rule()
        other = AlertService()
        other._get_session = lambda: self.Session()
        with patch("shared.utils.alert_service.post_json", return_value=(True, "ok")) as post:
            self.service.evaluate_all()
            other.evaluate_all()
        self.assertEqual(post.call_count, 1)
        session = self.Session()
        self.assertEqual(session.get(AlertRule, rule_id).fire_count, 1)
        session.close()

    def test_disabled_rules_are_skipped(self):
        self.token_service.get_error_count_since.return_value = 99
        self._rule(is_enabled=False)
        with patch("shared.utils.alert_service.post_json", return_value=(True, "ok")) as post:
            self.assertEqual(self.service.evaluate_all(), [])
        post.assert_not_called()


class TestConditions(_AlertTestCase):

    def test_guardrail_condition_reads_the_guardrail_log(self):
        self.guardrail_service.count_triggers_since.return_value = 12
        self._rule(condition_type="guardrail_count",
                   condition_config={"threshold": 10, "window_minutes": 60})
        with patch("shared.utils.alert_service.post_json", return_value=(True, "ok")) as post:
            fired = self.service.evaluate_all()
        self.assertEqual(len(fired), 1)
        self.assertEqual(post.call_args.args[1]["event"], "guardrail_alert")

    def test_project_scope_expands_to_its_agents(self):
        session = self.Session()
        session.add(AgentConfig(name="a1", type="llm", project_id=5))
        session.add(AgentConfig(name="a2", type="llm", project_id=5))
        session.commit()
        session.close()
        self.guardrail_service.count_triggers_since.return_value = 0
        self._rule(scope="project", scope_id="5", condition_type="guardrail_count",
                   condition_config={"threshold": 1, "window_minutes": 60})
        self.service.evaluate_all()
        names = self.guardrail_service.count_triggers_since.call_args.kwargs["agent_names"]
        self.assertEqual(sorted(names), ["a1", "a2"])

    def test_budget_limit_falls_back_to_rate_limit_config(self):
        session = self.Session()
        session.add(RateLimitConfig(scope="agent", scope_id="a1", tokens_per_day=1000))
        session.commit()
        session.close()
        self.token_service.get_agent_tokens_since.return_value = 950
        self._rule(condition_type="budget_threshold",
                   condition_config={"threshold_pct": 90, "period": "day"})
        with patch("shared.utils.alert_service.post_json", return_value=(True, "ok")) as post:
            fired = self.service.evaluate_all()
        self.assertEqual(len(fired), 1)
        payload = post.call_args.args[1]
        # The historical event name keeps existing webhook consumers working
        self.assertEqual(payload["event"], "rate_limit_alert")
        self.assertEqual(payload["limit"], 1000)
        self.assertEqual(payload["value"], 95)

    def test_budget_rule_without_any_limit_is_inert(self):
        self.token_service.get_agent_tokens_since.return_value = 5000
        self._rule(condition_type="budget_threshold",
                   condition_config={"threshold_pct": 50, "period": "day"})
        with patch("shared.utils.alert_service.post_json", return_value=(True, "ok")) as post:
            self.assertEqual(self.service.evaluate_all(), [])
        post.assert_not_called()

    def test_budget_threshold_does_not_refire_within_a_period(self):
        self.token_service.get_agent_tokens_since.return_value = 950
        rule_id = self._rule(cooldown_seconds=0, condition_type="budget_threshold",
                             condition_config={"threshold_pct": 90, "period": "day",
                                               "token_limit": 1000})
        with patch("shared.utils.alert_service.post_json", return_value=(True, "ok")) as post:
            self.service.evaluate_all()
            self.service.evaluate_all()
        self.assertEqual(post.call_count, 1)
        session = self.Session()
        state = session.get(AlertRule, rule_id).get_last_state()
        self.assertIn(90, state["fired_thresholds"])
        session.close()

    def test_budget_threshold_refires_in_a_new_period(self):
        self.token_service.get_agent_tokens_since.return_value = 950
        rule_id = self._rule(cooldown_seconds=0, condition_type="budget_threshold",
                             condition_config={"threshold_pct": 90, "period": "day",
                                               "token_limit": 1000})
        with patch("shared.utils.alert_service.post_json", return_value=(True, "ok")) as post:
            self.service.evaluate_all()
            session = self.Session()
            rule = session.get(AlertRule, rule_id)
            rule.set_last_state({"period_key": "1999-01-01", "fired_thresholds": [90]})
            session.commit()
            session.close()
            self.service.evaluate_all()
        self.assertEqual(post.call_count, 2)


class TestDelivery(_AlertTestCase):

    def test_email_destination_uses_the_email_sender(self):
        self.token_service.get_error_count_since.return_value = 5
        self._rule(destination_type="email", destination_config={"to": "ops@example.com"})
        with patch("shared.utils.alert_service.send_email", return_value=(True, "sent")) as send:
            self.service.evaluate_all()
        self.assertEqual(send.call_args.args[0], "ops@example.com")

    def test_delivery_failure_is_recorded_on_the_rule(self):
        self.token_service.get_error_count_since.return_value = 5
        rule_id = self._rule()
        with patch("shared.utils.alert_service.post_json", return_value=(False, "ConnectError: refused")):
            self.assertEqual(self.service.evaluate_all(), [])
        session = self.Session()
        rule = session.get(AlertRule, rule_id)
        self.assertIn("ConnectError", rule.last_error)
        # The attempt still consumed the cooldown — a dead endpoint must not be retried in a loop
        self.assertIsNotNone(rule.last_fired_at)
        session.close()

    def test_one_broken_rule_does_not_stop_the_pass(self):
        self.token_service.get_error_count_since.return_value = 5
        self._rule(name="broken", condition_type="agent_error_count")
        good_id = self._rule(name="good", scope_id="a2")
        original = self.service._measure

        def flaky(session, rule):
            if rule.name == "broken":
                raise RuntimeError("measurement exploded")
            return original(session, rule)

        self.service._measure = flaky
        with patch("shared.utils.alert_service.post_json", return_value=(True, "ok")):
            fired = self.service.evaluate_all()
        self.assertEqual([f["rule_id"] for f in fired], [good_id])

    def test_evaluate_all_without_a_database_returns_empty(self):
        self.service._get_session = lambda: None
        self.assertEqual(self.service.evaluate_all(), [])

    def test_test_fire_does_not_consume_the_cooldown(self):
        self.token_service.get_error_count_since.return_value = 5
        rule_id = self._rule()
        with patch("shared.utils.alert_service.post_json", return_value=(True, "ok")):
            result = self.service.evaluate_rule(rule_id, force=True)
        self.assertTrue(result["would_fire"])
        session = self.Session()
        rule = session.get(AlertRule, rule_id)
        self.assertIsNone(rule.last_fired_at)
        self.assertEqual(rule.fire_count, 0)
        session.close()


class TestGuardrailAggregation(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.now = datetime.now(timezone.utc)

        from shared.utils.guardrail_log_service import GuardrailLogService
        self.service = GuardrailLogService()
        self.service.db_client = MagicMock()
        self.service.db_client.get_session.side_effect = lambda: self.Session()

    def tearDown(self):
        self.engine.dispose()

    def _hit(self, agent_name="a1", minutes_ago=1, guardrail_type="prompt_injection",
             action_taken="block"):
        session = self.Session()
        session.add(GuardrailLog(
            request_id=f"r{minutes_ago}{agent_name}{guardrail_type}", agent_name=agent_name,
            guardrail_type=guardrail_type, phase="input", action_taken=action_taken,
            timestamp=self.now - timedelta(minutes=minutes_ago)))
        session.commit()
        session.close()

    def test_counts_within_the_window(self):
        self._hit(minutes_ago=1)
        self._hit(minutes_ago=2)
        self._hit(minutes_ago=200)
        self.assertEqual(self.service.count_triggers_since(self.now - timedelta(minutes=60)), 2)

    def test_filters_by_agent_type_and_action(self):
        self._hit(agent_name="a1", guardrail_type="prompt_injection", action_taken="block")
        self._hit(agent_name="a2", guardrail_type="prompt_injection", action_taken="block")
        self._hit(agent_name="a1", guardrail_type="content_policy", action_taken="warn")
        since = self.now - timedelta(minutes=60)
        self.assertEqual(self.service.count_triggers_since(since, agent_names=["a1"]), 2)
        self.assertEqual(
            self.service.count_triggers_since(since, guardrail_type="content_policy"), 1)
        self.assertEqual(self.service.count_triggers_since(since, action_taken="block"), 2)

    def test_empty_agent_list_counts_nothing(self):
        self._hit()
        self.assertEqual(
            self.service.count_triggers_since(self.now - timedelta(minutes=60), agent_names=[]), 0)


if __name__ == '__main__':
    unittest.main()
