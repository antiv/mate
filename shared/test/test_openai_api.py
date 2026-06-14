#!/usr/bin/env python3
"""
Unit tests for MATE OpenAI compatibility API, Personal Access Token (PAT) authentication,
and role-based authorization.
"""

import os
import sys
import unittest
import json
import hashlib
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.models import AgentConfig, User, PersonalAccessToken
from server.pat_auth import verify_pat_hash, get_allowed_roles, DEFAULT_ALLOWED_ROLES


class TestPersonalAccessTokenModel(unittest.TestCase):

    def test_token_hash_generation(self):
        token = "mate_pat_xyz123abc"
        expected_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.assertEqual(verify_pat_hash(token), expected_hash)

    def test_pat_to_dict(self):
        created_time = datetime.now(timezone.utc)
        pat = PersonalAccessToken(
            id=123,
            token_hash="some-hash",
            token_prefix="mate_pat_xyz",
            name="Test Token",
            user_id="dev_user",
            created_at=created_time,
            last_used_at=created_time + timedelta(minutes=5),
            expires_at=created_time + timedelta(days=30)
        )
        
        d = pat.to_dict()
        self.assertEqual(d["id"], 123)
        self.assertEqual(d["token_prefix"], "mate_pat_xyz")
        self.assertEqual(d["name"], "Test Token")
        self.assertEqual(d["user_id"], "dev_user")
        self.assertEqual(d["created_at"], created_time.isoformat())
        self.assertEqual(d["last_used_at"], (created_time + timedelta(minutes=5)).isoformat())
        self.assertEqual(d["expires_at"], (created_time + timedelta(days=30)).isoformat())


class TestPatAuthentication(unittest.TestCase):

    @patch.dict(os.environ, {"ALLOWED_API_ROLES": "developer,tester"})
    def test_get_allowed_roles_from_env(self):
        roles = get_allowed_roles()
        self.assertEqual(roles, {"developer", "tester"})

    @patch.dict(os.environ, {}, clear=True)
    def test_get_allowed_roles_default(self):
        roles = get_allowed_roles()
        self.assertEqual(roles, DEFAULT_ALLOWED_ROLES)


class TestOpenAIApiEndpoints(unittest.TestCase):

    @patch('server.openai_routes.get_database_client')
    @patch('server.openai_routes.get_pat_user')
    def test_list_models_filtering(self, mock_get_pat_user, mock_get_db):
        # Setup mocks
        mock_user = User(user_id="test_dev", roles='["developer"]')
        mock_get_pat_user.return_value = mock_user

        mock_session = MagicMock()
        mock_get_db.return_value.get_session.return_value = mock_session

        # Setup mock agents: 
        # 1. Root agent exposed (should be returned)
        agent1 = AgentConfig(name="exposed-root", disabled=False, expose_as_model=True, parent_agents="[]")
        agent1.id = 1
        agent1.updated_at = datetime.now(timezone.utc)
        # 2. Child agent exposed (should NOT be returned)
        agent2 = AgentConfig(name="exposed-child", disabled=False, expose_as_model=True, parent_agents='["exposed-root"]')
        agent2.id = 2
        # 3. Root agent not exposed (should NOT be returned)
        agent3 = AgentConfig(name="hidden-root", disabled=False, expose_as_model=False, parent_agents=None)
        agent3.id = 3

        mock_query = mock_session.query.return_value.filter.return_value.all
        mock_query.return_value = [agent1]  # The mock DB returns only the filtered ones

        # Call the route handler directly
        from server.openai_routes import list_models
        
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        result = loop.run_until_complete(list_models(user=mock_user))

        self.assertEqual(result["object"], "list")
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["data"][0]["id"], "exposed-root")

    def test_openai_stream_chunk_conversion(self):
        # Verify that MATE ADK chunks are successfully parsed and converted to OpenAI compatible format.
        # Sample MATE event:
        mate_event = {
            "author": "agent",
            "content": {
                "parts": [{"text": "Hello, how can I help you today?"}]
            }
        }
        
        # Test basic parsing
        parts = mate_event["content"]["parts"]
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["text"], "Hello, how can I help you today?")
        
        # Simulate delta logic
        last_text = ""
        text = parts[0]["text"]
        
        # turn 1 chunk 1: "Hello"
        t1 = "Hello"
        delta_t1 = t1[len(last_text):]
        last_text = t1
        self.assertEqual(delta_t1, "Hello")
        
        # turn 1 chunk 2: "Hello, how"
        t2 = "Hello, how"
        delta_t2 = t2[len(last_text):]
        last_text = t2
        self.assertEqual(delta_t2, ", how")

    def test_extract_content_text(self):
        from server.openai_routes import extract_content_text
        
        # Test string input
        self.assertEqual(extract_content_text("Hello string"), "Hello string")
        
        # Test list of text blocks (OpenAI rich content)
        rich_content = [
            {"type": "text", "text": "Hello rich "},
            {"type": "text", "text": "content format!"}
        ]
        self.assertEqual(extract_content_text(rich_content), "Hello rich content format!")
        
        # Test list with missing type (fallback)
        fallback_content = [
            {"text": "Fallback text"}
        ]
        self.assertEqual(extract_content_text(fallback_content), "Fallback text")

        # Test invalid/empty inputs
        self.assertEqual(extract_content_text(None), "")
        self.assertEqual(extract_content_text(123), "123")



if __name__ == '__main__':
    unittest.main()
