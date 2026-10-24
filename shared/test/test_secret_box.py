#!/usr/bin/env python3
"""
Unit tests for secrets at rest (MATE_SECRET_KEY, shared/utils/secret_box.py).

With a key, the four secret columns and the key in a version snapshot are stored
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

from shared.utils import secret_box
from shared.utils.models import AgentConfig, AgentConfigVersion, AgentTrigger, Base, ChannelIntegration
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
        session.add_all([agent, integration, trigger])
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
                             ["sk-agent", TOKEN, "slack-signing", "whsec_x"])

    def test_without_a_key_nothing_changes(self):
        with without_key():
            self._add_rows()
            self.assertEqual(self._raw_secrets(), ["sk-agent", TOKEN, "slack-signing", "whsec_x"])
            self.assertEqual(self._read_secrets(), ["sk-agent", TOKEN, "slack-signing", "whsec_x"])

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
            })
            for stored in self._raw_secrets():
                self.assertTrue(stored.startswith(secret_box.PREFIX), stored)
            self.assertEqual(self._read_secrets(), ["sk-agent", TOKEN, "slack-signing", "whsec_x"])

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
            self.assertEqual(secret_box.count_unencrypted_secrets(session), 4)
            session.close()
            self.assertEqual(self._raw_secrets(), ["sk-agent", TOKEN, "slack-signing", "whsec_x"])

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


if __name__ == "__main__":
    unittest.main()
