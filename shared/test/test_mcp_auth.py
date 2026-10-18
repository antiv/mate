#!/usr/bin/env python3
"""
The agent MCP servers run agents on the server's provider keys and the Google
Drive MCP server reads Drive with the server's service account, so every route
but health and CORS preflight needs the same callers as the OpenAI-compatible
API (get_pat_user). An agent's routes, and the GET /mcp listing, also honour the
agent's allowed_for_roles.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.mcp import mcp_auth
from shared.utils.mcp.agent_mcp_manager import AgentMCPManager
from shared.utils.mcp.agent_mcp_server import AgentMCPServer
from shared.utils.mcp.google_drive_mcp_server import GoogleDriveMCPServer

LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
TOKEN = "mate_pat_good"


class _AuthPatches(unittest.TestCase):
    """get_pat_user accepts only TOKEN; agent_allowed answers from self.allowed."""

    def setUp(self):
        self.allowed = {"helper"}

        async def fake_get_pat_user(request: Request):
            if request.headers.get("Authorization") != f"Bearer {TOKEN}":
                raise HTTPException(status_code=401, detail="Invalid Personal Access Token")
            return MagicMock(user_id="dev")

        for target, kwargs in (
            ("server.pat_auth.get_pat_user", {"side_effect": fake_get_pat_user}),
            ("shared.utils.mcp.agent_mcp_server.agent_allowed",
             {"side_effect": lambda user_id, name: name in self.allowed}),
            ("shared.utils.mcp.agent_mcp_manager.agent_allowed",
             {"side_effect": lambda user_id, name: name in self.allowed}),
        ):
            p = patch(target, **kwargs)
            p.start()
            self.addCleanup(p.stop)

    def call(self, method, path, token=None):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        if method == "post":
            return self.client.post(path, json=LIST, headers=headers)
        return self.client.get(path, headers=headers)


class TestAgentMcpAuth(_AuthPatches):

    def setUp(self):
        super().setUp()
        app = FastAPI()
        AgentMCPServer(app, "helper", "Helps")
        AgentMCPServer(app, "secret", "Admins only")
        self.client = TestClient(app)

    ROUTES = (("post", "/initialize"), ("get", "/sse"), ("post", "/tools/list"),
              ("post", "/tools/call"), ("get", ""), ("post", ""))

    def test_routes_need_a_caller(self):
        for method, suffix in self.ROUTES:
            for token in (None, "mate_pat_wrong"):
                resp = self.call(method, f"/agents/helper/mcp{suffix}", token)
                self.assertEqual(resp.status_code, 401, (suffix, token))

    def test_caller_without_access_to_the_agent_is_refused(self):
        for method, suffix in self.ROUTES:
            resp = self.call(method, f"/agents/secret/mcp{suffix}", TOKEN)
            self.assertEqual(resp.status_code, 403, suffix)

    def test_caller_with_access_gets_the_tools(self):
        resp = self.call("post", "/agents/helper/mcp", TOKEN)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["result"]["tools"][0]["name"], "call_helper_agent")

    def test_health_and_preflight_stay_public(self):
        self.assertEqual(self.client.get("/agents/secret/mcp/health").status_code, 200)
        self.assertEqual(self.client.options("/agents/secret/mcp").status_code, 200)


class TestGoogleDriveMcpAuth(_AuthPatches):

    def setUp(self):
        super().setUp()
        app = FastAPI()
        GoogleDriveMCPServer(app, gdrive_mcp_available=True)
        self.client = TestClient(app)

    def test_routes_need_a_caller(self):
        for method, path in (("post", "/gdrive/mcp/initialize"), ("get", "/gdrive/mcp/sse"),
                             ("post", "/gdrive/mcp/tools/list"), ("post", "/gdrive/mcp/tools/call"),
                             ("get", "/gdrive/mcp"), ("post", "/gdrive/mcp")):
            self.assertEqual(self.call(method, path).status_code, 401, path)

    def test_caller_gets_the_tools(self):
        resp = self.call("post", "/gdrive/mcp/tools/list", TOKEN)
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["result"]["tools"])

    def test_health_stays_public(self):
        self.assertEqual(self.client.get("/gdrive/mcp/health").status_code, 200)


class TestMcpListing(_AuthPatches):

    def setUp(self):
        super().setUp()
        app = FastAPI()
        with patch("shared.utils.mcp.agent_mcp_manager.get_database_client"):
            manager = AgentMCPManager(app)
        for name in ("helper", "secret"):
            manager.agent_mcp_servers[name] = AgentMCPServer(app, name, f"{name} agent")
        self.client = TestClient(app)

    def test_listing_needs_a_caller(self):
        self.assertEqual(self.call("get", "/mcp").status_code, 401)

    def test_lists_only_the_agents_the_caller_may_use(self):
        resp = self.call("get", "/mcp", TOKEN)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"agents": [{
            "name": "helper",
            "description": "helper agent",
            "url": "http://testserver/agents/helper/mcp",
        }]})


class TestAgentAllowed(unittest.TestCase):

    def check(self, allowed_for_roles):
        config = MagicMock(allowed_for_roles=allowed_for_roles)
        session = MagicMock()
        session.query.return_value.filter_by.return_value.first.return_value = config
        rbac = MagicMock()
        rbac.check_agent_access.return_value = (True, None)
        with patch("shared.utils.database_client.get_database_client") as db, \
                patch("shared.utils.rbac_middleware.get_rbac_middleware", return_value=rbac):
            db.return_value.get_session.return_value = session
            self.assertTrue(mcp_auth.agent_allowed("dev", "helper"))
        session.close.assert_called_once()
        return rbac.check_agent_access.call_args.args

    def test_passes_the_agents_roles_to_rbac(self):
        self.assertEqual(self.check('["developer"]'),
                         ("dev", {"name": "helper", "allowed_for_roles": ["developer"]}))

    def test_invalid_roles_mean_none_so_admin_only(self):
        self.assertEqual(self.check("not json")[1]["allowed_for_roles"], [])


if __name__ == "__main__":
    unittest.main()
