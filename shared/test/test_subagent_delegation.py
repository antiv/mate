#!/usr/bin/env python3
"""
Unit and integration tests for dynamic real-time subagent delegation (delegate_subtasks).
"""

import asyncio
import json
import os
import sys
import unittest
from typing import Any, AsyncGenerator, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from google.adk.models import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.tools import FunctionTool
from google.genai import types

from shared.utils.tools.subagent_delegation_tool import (
    SubtaskSpec,
    SubagentDelegator,
    _CANONICAL_TOOL_MAP,
    _FORBIDDEN_SUBAGENT_TOOLS,
    _build_subagent_tools,
    _get_context_values,
    _resolve_subagent_model,
    create_subagent_delegation_tools_from_config,
)
from shared.utils.tools.tool_factory import ToolFactory


class MockTestLlm(BaseLlm):
    """Mock LLM for subagent execution tests."""

    model: str = "mock-test-model"
    response_text: str = "Mock subagent analysis completed."
    delay: float = 0.0

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text=self.response_text)],
            )
        )


class TestSubagentDelegationTool(unittest.IsolatedAsyncioTestCase):
    """Tests for dynamic subagent delegation and concurrency."""

    def setUp(self):
        self.tool_factory = ToolFactory()
        self.parent_config = {
            "name": "orchestrator_agent",
            "model_name": "gemini-2.5-pro",
            "project_id": 1,
            "tool_config": json.dumps({
                "subagent_delegation": {
                    "max_subagents": 3,
                    "timeout_seconds": 10.0,
                    "default_model": "gemini-2.5-flash",
                },
                "google_search": True,
                "memory_blocks": {"enabled": True, "blocks": ["project_notes"]},
            }),
        }

    def test_factory_creates_tool(self):
        """ToolFactory should instantiate delegate_subtasks when enabled."""
        tools = self.tool_factory.create_tools(self.parent_config)
        tool_names = [getattr(t, "name", getattr(t, "__name__", str(t))) for t in tools]
        self.assertIn("delegate_subtasks", tool_names)

    def test_factory_skips_tool_when_omitted(self):
        """ToolFactory should skip delegate_subtasks when not in tool_config."""
        cfg = {"name": "basic_agent", "tool_config": json.dumps({"google_search": True})}
        tools = self.tool_factory.create_tools(cfg)
        tool_names = [getattr(t, "name", getattr(t, "__name__", str(t))) for t in tools]
        self.assertNotIn("delegate_subtasks", tool_names)

    def test_subtask_spec_validation(self):
        """SubtaskSpec should validate required fields and default optional ones."""
        task = SubtaskSpec(
            name="research_aws",
            instruction="Compare EC2 prices",
            role="Cloud Analyst",
            tools=["google_search", "browser"],
            model="openrouter/deepseek/deepseek-chat",
        )
        self.assertEqual(task.name, "research_aws")
        self.assertEqual(task.instruction, "Compare EC2 prices")
        self.assertEqual(task.role, "Cloud Analyst")
        self.assertEqual(len(task.tools), 2)
        self.assertEqual(task.model, "openrouter/deepseek/deepseek-chat")

    def test_fork_bomb_prevention(self):
        """Subagents must never receive delegation tools, even if requested."""
        requested = ["google_search", "subagent_delegation", "delegate_subtasks", "browser"]
        safe_tools, assigned_names = _build_subagent_tools(requested, self.parent_config)

        self.assertNotIn("subagent_delegation", assigned_names)
        self.assertNotIn("delegate_subtasks", assigned_names)
        tool_names = [getattr(t, "name", getattr(t, "__name__", str(t))) for t in safe_tools]
        self.assertNotIn("delegate_subtasks", tool_names)
        self.assertNotIn("subagent_delegation", tool_names)

    def test_tool_alias_resolution_and_inheritance(self):
        """Aliases (e.g. 'search', 'memory') should map to canonical tools and inherit settings."""
        requested = ["search", "memory"]
        safe_tools, assigned_names = _build_subagent_tools(requested, self.parent_config)

        self.assertIn("google_search", assigned_names)
        self.assertIn("memory_blocks", assigned_names)

    def test_model_resolution(self):
        """Model should resolve task override, then delegator default, then parent model."""
        # 1. Explicit task model
        m1, name1 = _resolve_subagent_model("ollama_chat/llama3.2", "gemini-2.5-flash", self.parent_config)
        self.assertEqual(name1, "ollama_chat/llama3.2")

        # 2. Delegator default model
        m2, name2 = _resolve_subagent_model(None, "gemini-2.5-flash", self.parent_config)
        self.assertEqual(name2, "gemini-2.5-flash")

        # 3. Parent model fallback
        m3, name3 = _resolve_subagent_model(None, None, self.parent_config)
        self.assertEqual(name3, "gemini-2.5-pro")

    async def test_delegate_subtasks_empty_input(self):
        """Empty or invalid subtask list should return a structured error."""
        delegator = SubagentDelegator(self.parent_config, {"max_subagents": 3})
        res1 = await delegator.delegate_subtasks(tasks=[])
        self.assertEqual(res1["status"], "error")

        res2 = await delegator.delegate_subtasks(tasks="invalid json string")
        self.assertEqual(res2["status"], "error")

    async def test_delegate_subtasks_max_cap(self):
        """Tasks exceeding max_subagents should be safely capped."""
        delegator = SubagentDelegator(self.parent_config, {"max_subagents": 2})
        tasks = [
            SubtaskSpec(name=f"task_{i}", instruction="do work")
            for i in range(5)
        ]

        with patch("shared.utils.tools.subagent_delegation_tool._run_single_subagent", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = {"status": "success", "task_name": "task_x", "output": "done"}
            res = await delegator.delegate_subtasks(tasks=tasks)
            self.assertEqual(mock_run.call_count, 2)
            self.assertEqual(res["total_tasks"], 2)

    async def test_parallel_subagent_execution(self):
        """Multiple subagents should execute concurrently and return aggregated results."""
        delegator = SubagentDelegator(self.parent_config, {"max_subagents": 3, "timeout_seconds": 10.0})
        tasks = [
            SubtaskSpec(name="task_a", role="Analyst A", instruction="Find fact A", tools=[]),
            SubtaskSpec(name="task_b", role="Analyst B", instruction="Find fact B", tools=[]),
        ]

        # Patch _resolve_subagent_model to return our MockTestLlm
        with patch("shared.utils.tools.subagent_delegation_tool._resolve_subagent_model") as mock_resolve:
            mock_resolve.return_value = (MockTestLlm(response_text="Custom result"), "mock-test-model")

            res = await delegator.delegate_subtasks(tasks=tasks)

            self.assertEqual(res["status"], "success")
            self.assertEqual(res["total_tasks"], 2)
            self.assertEqual(res["completed_tasks"], 2)
            self.assertEqual(res["failed_tasks"], 0)
            self.assertIn("task_a", res["results"])
            self.assertIn("task_b", res["results"])
            self.assertEqual(res["results"]["task_a"]["output"], "Custom result")
            self.assertEqual(res["results"]["task_b"]["output"], "Custom result")

    async def test_partial_failure_resilience(self):
        """A failure in one subagent must not crash the overall delegation."""
        delegator = SubagentDelegator(self.parent_config, {"max_subagents": 2, "timeout_seconds": 10.0})
        tasks = [
            SubtaskSpec(name="task_good", instruction="Good task"),
            SubtaskSpec(name="task_bad", instruction="Bad task"),
        ]

        async def fake_run(task, **kwargs):
            if task.name == "task_bad":
                return {"status": "error", "task_name": task.name, "output": None, "error": "Simulated failure"}
            return {"status": "success", "task_name": task.name, "output": "Success text", "error": None}

        with patch("shared.utils.tools.subagent_delegation_tool._run_single_subagent", side_effect=fake_run):
            res = await delegator.delegate_subtasks(tasks=tasks)

            self.assertEqual(res["status"], "success")
            self.assertEqual(res["total_tasks"], 2)
            self.assertEqual(res["completed_tasks"], 1)
            self.assertEqual(res["failed_tasks"], 1)
            self.assertEqual(res["results"]["task_good"]["status"], "success")
            self.assertEqual(res["results"]["task_bad"]["status"], "error")
            self.assertEqual(res["results"]["task_bad"]["error"], "Simulated failure")

    async def test_timeout_handling(self):
        """Subagents that exceed timeout_seconds should return a timeout status."""
        delegator = SubagentDelegator(self.parent_config, {"max_subagents": 1, "timeout_seconds": 0.05})
        tasks = [
            SubtaskSpec(name="task_slow", instruction="Slow task"),
        ]

        # Use MockTestLlm with delay > timeout
        with patch("shared.utils.tools.subagent_delegation_tool._resolve_subagent_model") as mock_resolve:
            mock_resolve.return_value = (MockTestLlm(delay=0.2), "mock-test-model")
            res = await delegator.delegate_subtasks(tasks=tasks)

            self.assertEqual(res["status"], "success")
            self.assertEqual(res["failed_tasks"], 1)
            self.assertEqual(res["results"]["task_slow"]["status"], "timeout")
            self.assertIn("timed out", res["results"]["task_slow"]["error"])

    async def test_subagent_with_tool_call_loop(self):
        """A subagent should be able to call its assigned tool and return final synthesized text."""
        def dummy_calc(x: int, y: int) -> int:
            """Add two numbers."""
            return x + y

        class ToolCallingLlm(BaseLlm):
            model: str = "mock-tool-caller"
            calls: int = 0
            async def generate_content_async(self, llm_request, stream=False):
                self.calls += 1
                if self.calls == 1:
                    yield LlmResponse(
                        content=types.Content(
                            role="model",
                            parts=[types.Part.from_function_call(name="dummy_calc", args={"x": 5, "y": 7})],
                        )
                    )
                else:
                    yield LlmResponse(
                        content=types.Content(
                            role="model",
                            parts=[types.Part.from_text(text="The calculated total is 12.")],
                        )
                    )

        delegator = SubagentDelegator(self.parent_config, {"max_subagents": 1, "timeout_seconds": 10.0})
        tasks = [SubtaskSpec(name="math_worker", instruction="Calculate 5 + 7")]

        with patch("shared.utils.tools.subagent_delegation_tool._resolve_subagent_model") as mock_resolve, \
             patch("shared.utils.tools.subagent_delegation_tool._build_subagent_tools") as mock_build_tools:
            mock_resolve.return_value = (ToolCallingLlm(), "mock-tool-caller")
            mock_build_tools.return_value = ([dummy_calc], ["dummy_calc"])

            res = await delegator.delegate_subtasks(tasks=tasks)
            self.assertEqual(res["status"], "success")
            self.assertEqual(res["results"]["math_worker"]["status"], "success")
            self.assertIn("12", res["results"]["math_worker"]["output"])

    def test_context_values_extraction(self):
        """_get_context_values should extract app_name, user_id, and session_id properly."""
        mock_ctx = MagicMock()
        mock_ctx._invocation_context = None
        mock_ctx.session = None
        mock_ctx.app_name = "test_app"
        mock_ctx.user_id = "test_user"
        mock_ctx.session_id = "test_session_123"

        app_name, user_id, session_id = _get_context_values(mock_ctx)
        self.assertEqual(app_name, "test_app")
        self.assertEqual(user_id, "test_user")
        self.assertEqual(session_id, "test_session_123")

        # Test fallback with None
        app_name_fb, user_id_fb, session_id_fb = _get_context_values(None)
        self.assertEqual(app_name_fb, "unknown")
        self.assertEqual(user_id_fb, "default")
        self.assertEqual(session_id_fb, "default")

    def test_options_parsing(self):
        """create_subagent_delegation_tools_from_config should handle various config formats."""
        # 1. Boolean True
        tools_bool = create_subagent_delegation_tools_from_config({
            "tool_config": json.dumps({"subagent_delegation": True})
        })
        self.assertEqual(len(tools_bool), 1)

        # 2. Options dictionary in JSON
        tools_dict = create_subagent_delegation_tools_from_config({
            "tool_config": json.dumps({"subagent_delegation": {"max_subagents": 4, "timeout_seconds": 45}})
        })
        self.assertEqual(len(tools_dict), 1)

        # 3. Disabled / None
        tools_none = create_subagent_delegation_tools_from_config({
            "tool_config": json.dumps({"subagent_delegation": False})
        })
        self.assertEqual(len(tools_none), 0)

    def test_langgraph_tool_adaptation(self):
        """LangGraph adapt_tools should not skip delegate_subtasks."""
        from shared.utils.langgraph.tool_adapter import adapt_tools
        tools = create_subagent_delegation_tools_from_config({
            "tool_config": json.dumps({"subagent_delegation": True})
        })
        self.assertEqual(len(tools), 1)
        adapted = adapt_tools(tools)
        self.assertEqual(len(adapted), 1)
        self.assertEqual(adapted[0].__name__, "delegate_subtasks")



if __name__ == "__main__":
    unittest.main()
