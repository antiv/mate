#!/usr/bin/env python3
"""
The rate-limit middleware checks a /run_sse request against the agent's project,
not one the caller names.

It read project_id from the request body, so a caller could name a project without
a budget and have its request counted there instead of against the agent's own
project. It now passes no project, and the rate limit service looks it up from the
agent (covered in test_usd_budgets).
"""

import json
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from server import rate_limit_middleware
from shared.utils.rate_limit_service import RateLimitResult


class TestProjectFromAgent(unittest.TestCase):

    def setUp(self):
        self.service = MagicMock()
        self.service.check_request_limit = AsyncMock(
            return_value=(RateLimitResult(allowed=True, action="warn", message=""), None))
        self.service.record_request = AsyncMock()
        patcher = patch.object(rate_limit_middleware, "get_rate_limit_service", return_value=self.service)
        patcher.start()
        self.addCleanup(patcher.stop)

        app = FastAPI()
        app.add_middleware(rate_limit_middleware.RateLimitMiddleware)

        @app.post("/run_sse")
        async def run_sse():
            return JSONResponse({"ok": True})

        self.client = TestClient(app)

    def _post(self, body):
        return self.client.post("/run_sse", content=json.dumps(body),
                                headers={"Content-Type": "application/json"})

    def test_a_project_in_the_body_is_ignored(self):
        resp = self._post({"app_name": "seller", "user_id": "u1", "project_id": 99})
        self.assertEqual(resp.status_code, 200)
        kwargs = self.service.check_request_limit.call_args.kwargs
        self.assertEqual(kwargs["user_id"], "u1")
        self.assertEqual(kwargs["agent_name"], "seller")
        self.assertIsNone(kwargs.get("project_id"))

    def test_the_camel_case_spelling_is_ignored_too(self):
        self._post({"appName": "seller", "userId": "u1", "projectId": 99})
        self.assertIsNone(self.service.check_request_limit.call_args.kwargs.get("project_id"))


if __name__ == "__main__":
    unittest.main()
