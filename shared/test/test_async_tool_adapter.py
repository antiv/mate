#!/usr/bin/env python3
"""
Tests for the async tool adapter (sync tools offloaded via asyncio.to_thread)
and for the rate limit service DB offload.
"""

import asyncio
import inspect
import sys
import os
import threading
import time
import unittest
from unittest.mock import MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.adk.tools.base_tool import BaseTool
from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.function_tool import FunctionTool
from google.adk.tools.tool_context import ToolContext

from shared.utils.tools.async_tool_adapter import to_thread_tool


def sample_tool(city: str, count: int = 3, tool_context: ToolContext = None) -> dict:
    """Return weather info for a city."""
    return {"city": city, "count": count}


async def async_sample_tool(city: str) -> dict:
    """An async tool."""
    return {"city": city}


class _DummyTool(BaseTool):
    def __init__(self):
        super().__init__(name="dummy", description="dummy tool")


class _DummyToolset(BaseToolset):
    async def get_tools(self, readonly_context=None):
        return []

    async def close(self):
        pass


class TestToThreadToolMetadata(unittest.TestCase):

    def test_preserves_metadata_and_signature(self):
        wrapped = to_thread_tool(sample_tool)
        self.assertIsNot(wrapped, sample_tool)
        self.assertEqual(wrapped.__name__, sample_tool.__name__)
        self.assertEqual(wrapped.__doc__, sample_tool.__doc__)
        self.assertEqual(wrapped.__annotations__, sample_tool.__annotations__)
        self.assertEqual(inspect.signature(wrapped), inspect.signature(sample_tool))
        self.assertTrue(inspect.iscoroutinefunction(wrapped))

    def test_identical_adk_declaration(self):
        """The LLM must see exactly the same function declaration."""
        original_decl = FunctionTool(sample_tool)._get_declaration()
        wrapped_decl = FunctionTool(to_thread_tool(sample_tool))._get_declaration()
        self.assertEqual(original_decl.model_dump(), wrapped_decl.model_dump())

    def test_passthrough_identity(self):
        """Async callables, BaseTool and BaseToolset instances stay untouched."""
        self.assertIs(to_thread_tool(async_sample_tool), async_sample_tool)
        dummy_tool = _DummyTool()
        self.assertIs(to_thread_tool(dummy_tool), dummy_tool)
        dummy_toolset = _DummyToolset()
        self.assertIs(to_thread_tool(dummy_toolset), dummy_toolset)
        self.assertEqual(to_thread_tool("not-callable"), "not-callable")

    def test_generator_functions_untouched(self):
        def gen_tool():
            yield 1

        self.assertIs(to_thread_tool(gen_tool), gen_tool)


class TestToThreadToolExecution(unittest.IsolatedAsyncioTestCase):

    async def test_tool_context_passthrough_and_off_loop_thread(self):
        seen = {}

        def recording_tool(city: str, tool_context: ToolContext = None) -> dict:
            """Record execution context."""
            seen["tool_context"] = tool_context
            seen["thread_id"] = threading.get_ident()
            return {"city": city}

        wrapped = FunctionTool(to_thread_tool(recording_tool))
        mock_ctx = MagicMock(spec=ToolContext)
        result = await wrapped.run_async(args={"city": "Belgrade"}, tool_context=mock_ctx)

        self.assertEqual(result, {"city": "Belgrade"})
        self.assertIs(seen["tool_context"], mock_ctx)
        self.assertNotEqual(seen["thread_id"], threading.get_ident())

    async def test_event_loop_stays_responsive(self):
        def slow_tool() -> str:
            """Sleep synchronously."""
            time.sleep(0.5)
            return "done"

        wrapped = to_thread_tool(slow_tool)
        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        hb = asyncio.create_task(heartbeat())
        result = await wrapped()
        hb.cancel()

        self.assertEqual(result, "done")
        # Unwrapped, the sync sleep would block the loop and ticks would be 0
        self.assertGreaterEqual(ticks, 20)


class TestToolFactoryIntegration(unittest.TestCase):

    def test_factory_tools_are_async_with_preserved_names(self):
        from shared.utils.tools.tool_factory import ToolFactory

        result = ToolFactory().create_tools({})
        by_name = {getattr(t, "__name__", None): t for t in result}
        for name in ("update_user_profile", "get_user_profile"):
            self.assertIn(name, by_name)
            self.assertTrue(
                inspect.iscoroutinefunction(by_name[name]),
                f"{name} should be offloaded to a thread (async adapter)",
            )

    def test_confirmation_wrapped_tool_uses_async_adapter(self):
        import json
        from shared.utils.tools.tool_factory import ToolFactory

        config = {
            'name': 'test_agent',
            'tool_config': json.dumps({'require_confirmation': ['update_user_profile']})
        }
        result = ToolFactory().create_tools(config)
        wrapped = [t for t in result if isinstance(t, FunctionTool) and t.name == 'update_user_profile']
        self.assertEqual(len(wrapped), 1)
        self.assertTrue(inspect.iscoroutinefunction(wrapped[0].func))


class TestRateLimitOffload(unittest.IsolatedAsyncioTestCase):

    def _make_service(self, cfg, token_delay: float, tokens: int):
        from shared.utils.rate_limit_service import RateLimitService

        svc = RateLimitService.__new__(RateLimitService)

        session = MagicMock()
        session.query.return_value.filter.return_value.first.return_value = cfg
        db_client = MagicMock()
        db_client.is_connected.return_value = True
        db_client.get_session.return_value = session
        svc.db_client = db_client

        def slow_tokens(user_id, since):
            time.sleep(token_delay)
            return tokens

        token_service = MagicMock()
        token_service.get_user_tokens_since.side_effect = slow_tokens
        svc.token_service = token_service
        svc._redis = None
        return svc, session

    async def test_blocking_db_work_runs_off_loop(self):
        from types import SimpleNamespace

        cfg = SimpleNamespace(
            requests_per_minute=None,
            tokens_per_hour=100,
            tokens_per_day=None,
            tokens_per_month=None,
            action_on_limit="block",
        )
        svc, session = self._make_service(cfg, token_delay=0.1, tokens=150)

        ticks = 0

        async def heartbeat():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        hb = asyncio.create_task(heartbeat())
        result, usage = await svc.check_request_limit("user1")
        hb.cancel()

        self.assertFalse(result.allowed)
        self.assertEqual(result.action, "block")
        self.assertEqual(usage.tokens_last_hour, 150)
        session.close.assert_called_once()
        # Three 0.1s token queries run in a worker thread; the loop must keep ticking
        self.assertGreaterEqual(ticks, 10)

    async def test_returns_plain_values_not_orm_objects(self):
        from types import SimpleNamespace

        cfg = SimpleNamespace(
            requests_per_minute=None,
            tokens_per_hour=None,
            tokens_per_day=None,
            tokens_per_month=None,
            action_on_limit="warn",
        )
        svc, _ = self._make_service(cfg, token_delay=0.0, tokens=5)

        data = await asyncio.to_thread(
            svc._collect_limit_data_sync, "user1", None, None, None
        )
        self.assertIsNotNone(data)
        configs, (t_hour, t_day, t_month) = data
        for scope, sid, plain in configs:
            self.assertIsInstance(plain, SimpleNamespace)
        self.assertEqual((t_hour, t_day, t_month), (5, 5, 5))


if __name__ == "__main__":
    unittest.main()
