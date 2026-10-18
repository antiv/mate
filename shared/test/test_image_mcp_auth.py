#!/usr/bin/env python3
"""
The image MCP endpoints spend the server's provider keys, so every route but the
health check needs a signed-in caller, the health check does not say which model
or keys are set, and callers cannot pick the model or its request parameters.
"""

import os
import sys
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.mcp.image_mcp_server import ImageMCPServer

CALL = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "generate_image_nano_banana",
                   "arguments": {"prompt": "x", "model_config": {
                       "model": "dall-e-3", "mock_response": "http://10.0.0.5/admin"}}}}


class TestImageMcpAuth(unittest.TestCase):

    def setUp(self):
        app = FastAPI()
        ImageMCPServer(app, image_mcp_available=True)
        self.client = TestClient(app)
        self.user = None
        p = patch("server.auth.get_dashboard_auth_user", side_effect=lambda request: self.user)
        p.start()
        self.addCleanup(p.stop)

    def test_routes_need_a_signed_in_caller(self):
        for method, path in (("post", "/images/mcp/tools/call"), ("post", "/images/mcp/tools/list"),
                             ("post", "/images/mcp/initialize"), ("get", "/images/mcp"),
                             ("post", "/images/mcp"), ("get", "/images/mcp/sse")):
            resp = getattr(self.client, method)(path, json=CALL) if method == "post" \
                else self.client.get(path)
            self.assertEqual(resp.status_code, 401, path)

    def test_health_is_public_but_says_nothing_about_the_setup(self):
        with patch("shared.utils.tools.image_tools.validate_image_generation_setup",
                   return_value=(False, "No API key for image model 'x'", {"default_model": "x",
                                                                         "missing_keys": ["K"]})):
            body = self.client.get("/images/mcp/health").json()
        self.assertEqual(set(body) - {"status", "service", "tools", "endpoint", "protocol"}, set())
        self.assertNotIn("x", str(body.get("status")))

    def test_callers_cannot_pick_the_model_or_its_parameters(self):
        self.user = "admin"
        gen = AsyncMock(return_value={"success": True})
        with patch("shared.utils.tools.image_tools._generate_image_internal", gen):
            resp = self.client.post("/images/mcp/tools/call", json=CALL)
        self.assertEqual(resp.status_code, 200)
        prompt, _ctx, model, config = gen.call_args.args
        self.assertEqual(model, "nano-banana")
        self.assertEqual(config, {})


class TestStartupCheck(unittest.TestCase):

    def test_availability_reads_the_current_setup_details(self):
        # The check used to read keys the setup no longer returns, and so always failed
        app = FastAPI()
        server = ImageMCPServer(app, image_mcp_available=False)
        with patch("shared.utils.tools.image_tools.validate_image_generation_setup",
                   return_value=(True, "", {"default_model": "dall-e-3", "provider": "openai",
                                            "source": "default", "missing_keys": []})):
            self.assertTrue(server.check_image_mcp_availability())
        self.assertTrue(server.image_mcp_available)


if __name__ == "__main__":
    unittest.main()
