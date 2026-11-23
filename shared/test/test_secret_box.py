#!/usr/bin/env python3
"""
Unit tests for secrets at rest (MATE_SECRET_KEY, shared/utils/secret_box.py).

With a key, the five secret columns and the key in a version snapshot are stored
encrypted and read back as plain text. Without one they are stored in the clear,
as before 1.6. Values written before the key was set stay readable and are
encrypted by encrypt_stored_secrets(). A value that does not decrypt reads as None.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils import config_secrets, secret_box
from shared.utils.models import (
    AgentConfig, AgentConfigVersion, AgentTrigger, AlertRule, Base, ChannelIntegration, WorkspaceProviderKey,
)
from shared.utils.trigger_runner import TriggerRunner

KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()
TOKEN = "xoxb-123-secret"


def with_key(key):
    return patch.dict(os.environ, {secret_box.ENV_VAR: key})


def without_key():
    env = dict(os.environ)
    env.pop(secret_box.ENV_VAR, None)
    return patch.dict(os.environ, env, clear=True)


class TestEncryptDecrypt(unittest.TestCase):

    def test_round_trip_with_a_key(self):
        with with_key(KEY):
            stored = secret_box.encrypt(TOKEN)
            self.assertTrue(stored.startswith(secret_box.PREFIX))
            self.assertNotIn(TOKEN, stored)
            self.assertEqual(secret_box.decrypt(stored), TOKEN)

    def test_no_key_stores_in_the_clear(self):
        with without_key():
            self.assertFalse(secret_box.key_configured())
            self.assertEqual(secret_box.encrypt(TOKEN), TOKEN)
            self.assertEqual(secret_box.decrypt(TOKEN), TOKEN)

    def test_a_plain_value_reads_as_itself_with_a_key(self):
        with with_key(KEY):
            self.assertEqual(secret_box.decrypt(TOKEN), TOKEN)

    def test_empty_values_pass_through(self):
        with with_key(KEY):
            self.assertIsNone(secret_box.encrypt(None))
            self.assertEqual(secret_box.encrypt(""), "")

    def test_an_encrypted_value_is_not_encrypted_twice(self):
        with with_key(KEY):
            stored = secret_box.encrypt(TOKEN)
            self.assertEqual(secret_box.encrypt(stored), stored)

    def test_another_key_or_no_key_reads_as_none(self):
        with with_key(KEY):
            stored = secret_box.encrypt(TOKEN)
        with with_key(OTHER_KEY), self.assertLogs("shared.utils.secret_box", "ERROR"):
            self.assertIsNone(secret_box.decrypt(stored))
        with without_key(), self.assertLogs("shared.utils.secret_box", "ERROR"):
            self.assertIsNone(secret_box.decrypt(stored))

    def test_production_refuses_to_start_without_a_key(self):
        with without_key():
            with self.assertRaises(RuntimeError) as raised:
                secret_box.check_key_at_startup(enforce=True)
            self.assertIn("MATE_ALLOW_INSECURE_DEFAULTS", str(raised.exception))
            # Elsewhere, or with the override, it warns
            with self.assertLogs("shared.utils.secret_box", "WARNING"):
                secret_box.check_key_at_startup(enforce=False)
        with with_key(KEY):
            secret_box.check_key_at_startup(enforce=True)

    def test_an_invalid_key_fails_loudly(self):
        with with_key("not-a-fernet-key"):
            with self.assertRaises(RuntimeError) as raised:
                secret_box.key_configured()
            self.assertIn("MATE_SECRET_KEY", str(raised.exception))


class _Db(unittest.TestCase):

    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                                    poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)

    def _add_rows(self):
        session = self.Session()
        agent = AgentConfig(name="a", type="llm", model_name="openai/x", project_id=1,
                            model_api_key="sk-agent")
        integration = ChannelIntegration(platform="slack", project_id=1, agent_name="a",
                                         bot_token=TOKEN, signing_secret="slack-signing")
        trigger = AgentTrigger(name="t", trigger_type="webhook", agent_name="a", project_id=1,
                               prompt="p", signing_secret="whsec_x")
        provider_key = WorkspaceProviderKey(workspace_id=1, provider="openrouter",
                                            api_key="sk-or-workspace")
        session.add_all([agent, integration, trigger, provider_key])
        session.commit()
        session.close()

    def _raw(self, sql):
        with self.engine.connect() as conn:
            return conn.execute(text(sql)).scalar()

    def _raw_secrets(self):
        return [
            self._raw("SELECT model_api_key FROM agents_config"),
            self._raw("SELECT bot_token FROM channel_integrations"),
            self._raw("SELECT signing_secret FROM channel_integrations"),
            self._raw("SELECT signing_secret FROM agent_triggers"),
            self._raw("SELECT api_key FROM workspace_provider_keys"),
        ]

    def _read_secrets(self):
        session = self.Session()
        try:
            integration = session.query(ChannelIntegration).one()
            return [
                session.query(AgentConfig).one().model_api_key,
                integration.bot_token,
                integration.signing_secret,
                session.query(AgentTrigger).one().signing_secret,
                session.query(WorkspaceProviderKey).one().api_key,
            ]
        finally:
            session.close()


class TestColumns(_Db):

    def test_with_a_key_the_columns_are_stored_encrypted_and_read_in_the_clear(self):
        with with_key(KEY):
            self._add_rows()
            for stored in self._raw_secrets():
                self.assertTrue(stored.startswith(secret_box.PREFIX), stored)
            self.assertEqual(self._read_secrets(),
                             ["sk-agent", TOKEN, "slack-signing", "whsec_x", "sk-or-workspace"])

    def test_without_a_key_nothing_changes(self):
        with without_key():
            self._add_rows()
            self.assertEqual(self._raw_secrets(), ["sk-agent", TOKEN, "slack-signing", "whsec_x", "sk-or-workspace"])
            self.assertEqual(self._read_secrets(), ["sk-agent", TOKEN, "slack-signing", "whsec_x", "sk-or-workspace"])

    def test_a_signature_verifies_against_the_decrypted_secret(self):
        with with_key(KEY):
            self._add_rows()
            secret = self._read_secrets()[3]
        import hashlib
        import hmac
        body = b'{"x": 1}'
        signature = "sha256=" + hmac.new(b"whsec_x", body, hashlib.sha256).hexdigest()
        self.assertTrue(TriggerRunner.verify_signature(secret, body, signature))

    def test_a_secret_that_does_not_decrypt_fails_closed(self):
        with with_key(KEY):
            self._add_rows()
        with with_key(OTHER_KEY), self.assertLogs("shared.utils.secret_box", "ERROR"):
            secret = self._read_secrets()[3]
        self.assertIsNone(secret)
        self.assertFalse(TriggerRunner.verify_signature(secret, b"{}", "sha256=00"))

    def test_saving_another_field_does_not_touch_an_unreadable_secret(self):
        with with_key(KEY):
            self._add_rows()
        before = self._raw("SELECT bot_token FROM channel_integrations")
        with without_key(), self.assertLogs("shared.utils.secret_box", "ERROR"):
            session = self.Session()
            session.query(ChannelIntegration).one().label = "renamed"
            session.commit()
            session.close()
        self.assertEqual(self._raw("SELECT bot_token FROM channel_integrations"), before)


class TestEncryptStoredSecrets(_Db):

    def test_rows_written_before_the_key_are_encrypted(self):
        with without_key():
            self._add_rows()
        with with_key(KEY):
            session = self.Session()
            counts = secret_box.encrypt_stored_secrets(session)
            session.close()
            self.assertEqual(counts, {
                "channel_integrations.bot_token": 1,
                "channel_integrations.signing_secret": 1,
                "agent_triggers.signing_secret": 1,
                "agents_config.model_api_key": 1,
                "workspace_provider_keys.api_key": 1,
            })
            for stored in self._raw_secrets():
                self.assertTrue(stored.startswith(secret_box.PREFIX), stored)
            self.assertEqual(self._read_secrets(), ["sk-agent", TOKEN, "slack-signing", "whsec_x", "sk-or-workspace"])

    def test_a_second_run_changes_nothing(self):
        with with_key(KEY):
            self._add_rows()
            before = self._raw_secrets()
            session = self.Session()
            self.assertEqual(secret_box.encrypt_stored_secrets(session), {})
            session.close()
            self.assertEqual(self._raw_secrets(), before)

    def test_without_a_key_it_only_counts(self):
        with without_key():
            self._add_rows()
            session = self.Session()
            self.assertEqual(secret_box.encrypt_stored_secrets(session), {})
            self.assertEqual(secret_box.count_unencrypted_secrets(session), 5)
            session.close()
            self.assertEqual(self._raw_secrets(), ["sk-agent", TOKEN, "slack-signing", "whsec_x", "sk-or-workspace"])

    def test_version_snapshots_written_before_the_key_are_encrypted(self):
        session = self.Session()
        session.add_all([
            AgentConfigVersion(agent_config_id=1, version_number=1,
                               config_snapshot=json.dumps({"name": "a", "model_api_key": "sk-old"})),
            AgentConfigVersion(agent_config_id=1, version_number=2,
                               config_snapshot=json.dumps({"name": "a", "model_api_key": None})),
            AgentConfigVersion(agent_config_id=1, version_number=3,
                               config_snapshot=json.dumps({"name": "a"})),
        ])
        session.commit()
        with without_key():
            self.assertEqual(secret_box.count_unencrypted_secrets(session), 1)
        session.close()
        with with_key(KEY):
            session = self.Session()
            self.assertEqual(secret_box.encrypt_stored_secrets(session),
                             {"agent_config_versions.config_snapshot": 1})
            self.assertEqual(secret_box.encrypt_stored_secrets(session), {})
            raw = self._raw("SELECT config_snapshot FROM agent_config_versions WHERE version_number = 1")
            self.assertNotIn("sk-old", raw)
            version = session.query(AgentConfigVersion).filter_by(version_number=1).one()
            self.assertEqual(version.get_snapshot(), {"name": "a", "model_api_key": "sk-old"})
            session.close()

    def test_null_and_empty_values_are_left_alone(self):
        with without_key():
            session = self.Session()
            session.add(ChannelIntegration(platform="slack", project_id=1, agent_name="a",
                                           bot_token=None, signing_secret=""))
            session.commit()
            self.assertEqual(secret_box.count_unencrypted_secrets(session), 0)
            session.close()
        with with_key(KEY):
            session = self.Session()
            secret_box.encrypt_stored_secrets(session)
            session.close()
        self.assertIsNone(self._raw("SELECT bot_token FROM channel_integrations"))
        self.assertEqual(self._raw("SELECT signing_secret FROM channel_integrations"), "")


class TestVersionSnapshots(_Db):

    def test_the_key_in_a_snapshot_is_stored_encrypted(self):
        with with_key(KEY):
            version = AgentConfigVersion(agent_config_id=1, version_number=1)
            version.set_snapshot({"name": "a", "model_api_key": "sk-agent"})
            self.assertNotIn("sk-agent", version.config_snapshot)
            self.assertEqual(version.get_snapshot(), {"name": "a", "model_api_key": "sk-agent"})

    def test_an_env_reference_and_no_key_round_trip(self):
        with with_key(KEY):
            version = AgentConfigVersion(agent_config_id=1, version_number=1)
            version.set_snapshot({"model_api_key": None})
            self.assertEqual(version.get_snapshot(), {"model_api_key": None})

    def test_a_snapshot_stored_in_the_clear_still_reads(self):
        with with_key(KEY):
            version = AgentConfigVersion(agent_config_id=1, version_number=1,
                                         config_snapshot=json.dumps({"model_api_key": "sk-old"}))
            self.assertEqual(version.get_snapshot()["model_api_key"], "sk-old")


class TestSecretsInJson(_Db):
    """The JSON columns, and the same fields of a version: only their secrets are encrypted."""

    SECRET = "Zk81mQpX0rT5vB2nL7wYc3"
    SERVERS = {"mcpServers": {"r": {"url": "https://x.example/mcp", "headers": {"Authorization": "Bearer " + SECRET}}}}
    TOOLS = {"shop": {"partner_key": "p1"}, "require_confirmation": {
        "tools": ["a"], "notify": {"type": "slack", "url": "https://hooks.slack.com/services/" + SECRET}}}
    SEND = {"url": "https://hooks.example.com/" + SECRET}

    def _add(self):
        session = self.Session()
        agent = AgentConfig(name="a", type="llm", model_name="openai/x", project_id=1,
                            mcp_servers_config=json.dumps(self.SERVERS), tool_config=json.dumps(self.TOOLS))
        trigger = AgentTrigger(name="t", trigger_type="cron", agent_name="a", project_id=1, prompt="p")
        trigger.set_output_config(self.SEND)
        rule = AlertRule(name="r", scope="global", condition_type="agent_error_count", destination_type="http")
        rule.set_destination_config(self.SEND)
        session.add_all([agent, trigger, rule])
        session.flush()
        version = AgentConfigVersion(agent_config_id=agent.id, version_number=1)
        version.set_snapshot({"name": "a", "mcp_servers_config": json.dumps(self.SERVERS),
                              "tool_config": json.dumps(self.TOOLS)})
        session.add(version)
        session.commit()
        session.close()

    def _stored(self):
        return [self._raw(sql) for sql in (
            "SELECT mcp_servers_config FROM agents_config", "SELECT tool_config FROM agents_config",
            "SELECT output_config FROM agent_triggers", "SELECT destination_config FROM alert_rules",
            "SELECT config_snapshot FROM agent_config_versions")]

    def _read(self):
        session = self.Session()
        try:
            agent = session.query(AgentConfig).one()
            snapshot = session.query(AgentConfigVersion).one().get_snapshot()
            return [json.loads(agent.mcp_servers_config), json.loads(agent.tool_config),
                    session.query(AgentTrigger).one().get_output_config(),
                    session.query(AlertRule).one().get_destination_config(),
                    json.loads(snapshot["mcp_servers_config"]), json.loads(snapshot["tool_config"])]
        finally:
            session.close()

    ALL = [SERVERS, TOOLS, SEND, SEND, SERVERS, TOOLS]

    def test_only_the_secrets_are_encrypted(self):
        with with_key(KEY):
            self._add()
            stored = self._stored()
            for value in stored:
                self.assertNotIn(self.SECRET, value)
                self.assertIn(secret_box.PREFIX, value)
            self.assertEqual(json.loads(stored[0])["mcpServers"]["r"]["url"], "https://x.example/mcp")
            # The sidebar finds a shop's partner_key with LIKE (features_in_use.py)
            self.assertIn('"partner_key": "p1"', stored[1])
            self.assertEqual(self._read(), self.ALL)

    def test_without_a_key_they_are_stored_as_written(self):
        with without_key():
            self._add()
            self.assertEqual(self._stored()[0], json.dumps(self.SERVERS))
            self.assertEqual(self._read(), self.ALL)

    def test_a_text_without_a_secret_is_stored_as_written(self):
        with with_key(KEY):
            for text_ in ('{ "mcpServers": {"r": {"url": "https://x.example", "headers": {"A": "${T}"}}} }', "not json", ""):
                self.assertEqual(config_secrets.encrypt_secrets("mcp_servers_config", text_), text_)

    def test_those_stored_before_are_encrypted_at_start_once_and_not_as_an_edit(self):
        with without_key():
            self._add()
            session = self.Session()
            self.assertEqual(secret_box.count_unencrypted_secrets(session), 5)
            session.close()
        before = [self._raw(f"SELECT updated_at FROM {t}") for t in ("agent_triggers", "alert_rules")]
        with with_key(KEY):
            session = self.Session()
            counts = secret_box.encrypt_stored_secrets(session)
            self.assertEqual(counts, {"agents_config.mcp_servers_config": 1, "agents_config.tool_config": 1,
                                      "agent_triggers.output_config": 1, "alert_rules.destination_config": 1,
                                      "agent_config_versions.config_snapshot": 1})
            self.assertEqual(secret_box.encrypt_stored_secrets(session), {})
            self.assertEqual(secret_box.count_unencrypted_secrets(session), 0)
            session.close()
            for value in self._stored():
                self.assertNotIn(self.SECRET, value)
            self.assertEqual(self._read(), self.ALL)
        self.assertEqual(before, [self._raw(f"SELECT updated_at FROM {t}")
                                  for t in ("agent_triggers", "alert_rules")])

    def test_another_key_leaves_the_rest_of_the_config(self):
        with with_key(KEY):
            self._add()
        with with_key(OTHER_KEY), self.assertLogs("shared.utils.secret_box", "ERROR"):
            servers, tools, *_ = self._read()
        self.assertEqual(servers["mcpServers"]["r"], {"url": "https://x.example/mcp", "headers": {"Authorization": ""}})
        self.assertEqual(tools["require_confirmation"]["tools"], ["a"])

    def test_the_version_still_matches_the_agent(self):
        from shared.utils.agent_config_identity import config_hash, config_snapshot
        session = self.Session()
        with without_key():
            agent = AgentConfig(name="a", type="llm", model_name="m", project_id=1,
                                mcp_servers_config=json.dumps(self.SERVERS, indent=2))
            session.add(agent)
            session.commit()
            version = AgentConfigVersion(agent_config_id=agent.id, version_number=1)
            version.set_snapshot(config_snapshot(agent))
            session.add(version)
            session.commit()
        with with_key(KEY):
            secret_box.encrypt_stored_secrets(session)
            session.expire_all()
            self.assertEqual(config_hash(version.get_snapshot()), config_hash(config_snapshot(agent)))
        session.close()


if __name__ == "__main__":
    unittest.main()
