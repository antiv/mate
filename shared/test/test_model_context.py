#!/usr/bin/env python3
"""
Unit tests for the context create_model() takes (1.6.0 item 5).

Every model is built for someone: an agent in a project, or the installation.
create_model() and the LangGraph runtime's create_chat_model() take that as a
ModelContext, so 2.0 can look up a workspace's provider keys from it in one place.
Provider keys still come from the environment.

The inventory test reads the source and fails on a call that does not pass a
context, so a new caller cannot forget it.
"""

import ast
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.utils import ModelContext, create_model, create_model_from_agent_config, model_context_of

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
FACTORIES = {"create_model", "create_chat_model"}
# Modules whose create_model is MATE's (pydantic's has the same name)
FACTORY_MODULES = ("utils", "utils.utils", "shared.utils", "shared.utils.utils",
                   "model_factory", "shared.utils.langgraph.model_factory")


def _sources():
    for folder in ("shared", "server", "agents"):
        for path in (PROJECT_ROOT / folder).rglob("*.py"):
            if "test" not in path.parts:
                yield path
    yield from PROJECT_ROOT.glob("*.py")


def _calls_without_context(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defines = {node.name for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef) and node.name in FACTORIES}
    imports = {alias.asname or alias.name
               for node in ast.walk(tree)
               if isinstance(node, ast.ImportFrom) and (node.module or "").endswith(FACTORY_MODULES)
               for alias in node.names if alias.name in FACTORIES}
    names = defines | imports
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in names
                and not any(kw.arg == "context" for kw in node.keywords)):
            yield f"{path.relative_to(PROJECT_ROOT)}:{node.lineno}"


class TestEveryCallerPassesAContext(unittest.TestCase):

    def test_no_call_leaves_the_context_out(self):
        missing = [where for path in _sources() for where in _calls_without_context(path)]
        self.assertEqual(missing, [], "Pass context=ModelContext(...) to create_model() "
                                      "and create_chat_model(); ModelContext() for the installation")

    def test_the_inventory_finds_the_known_callers(self):
        # Guards the test itself: if the import matching broke, it would pass on nothing
        callers = set()
        for path in _sources():
            text = path.read_text(encoding="utf-8")
            if any(f"{name}(" in text for name in FACTORIES):
                callers.add(path.relative_to(PROJECT_ROOT).as_posix())
        for expected in ("shared/utils/utils.py", "shared/utils/fallback_agent.py",
                         "shared/callbacks/model_fallback_callback.py",
                         "shared/utils/tools/subagent_delegation_tool.py",
                         "shared/utils/langgraph/agent_builder.py"):
            self.assertIn(expected, callers)


class TestModelContext(unittest.TestCase):

    def test_an_agents_context_comes_from_its_config(self):
        self.assertEqual(model_context_of({"name": "a", "project_id": 3}),
                         ModelContext(agent_name="a", project_id=3))

    def test_no_agent_is_the_installation(self):
        self.assertEqual(ModelContext().describe(), "installation")
        self.assertEqual(ModelContext("a", 3).describe(), "agent a, project 3")

    def test_create_model_logs_the_context(self):
        with self.assertLogs("shared.utils.utils", "INFO") as logs:
            create_model("openai/gpt-4o", context=ModelContext("a", 3))
            create_model("gemini-2.5-flash")
        self.assertIn("agent a, project 3", logs.output[0])
        self.assertIn("installation", logs.output[1])

    def test_keys_still_come_from_the_environment(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-env"}):
            model = create_model("openrouter/x/y", context=ModelContext("a", 3))
        self.assertEqual(model._additional_args.get("api_key"), "sk-env")


class TestCallersPassTheirContext(unittest.TestCase):

    def test_a_database_agent(self):
        with patch("shared.utils.utils.create_model") as factory:
            create_model_from_agent_config({"name": "a", "project_id": 3, "model_name": "openai/x"})
        self.assertEqual(factory.call_args.kwargs["context"], ModelContext("a", 3))

    def test_a_fallback_model_is_built_for_its_agent(self):
        from shared.callbacks.model_fallback_callback import make_model_fallback_callback
        with patch("shared.utils.utils.create_model") as factory:
            make_model_fallback_callback("a", "openai/x", "openai/y", project_id=3)
        self.assertEqual(factory.call_args.kwargs["context"], ModelContext("a", 3))

    def test_a_subagent_is_built_for_its_parent(self):
        from shared.utils.tools import subagent_delegation_tool
        with patch.object(subagent_delegation_tool, "create_model") as factory:
            subagent_delegation_tool._resolve_subagent_model(
                "openai/y", None, {"name": "parent", "project_id": 3, "model_name": "openai/x"})
        self.assertEqual(factory.call_args.kwargs["context"], ModelContext("parent", 3))

    def test_the_adk_build_passes_the_agents_project(self):
        from shared.utils.models import AgentConfig
        with patch("shared.utils.agent_manager.get_database_client", return_value=MagicMock()):
            from shared.utils.agent_manager import AgentManager
            manager = AgentManager()
        row = AgentConfig(name="ctx_agent", type="llm", model_name="openai/x", instruction="hi",
                          project_id=3, fallback_model="openai/y")
        with patch("shared.utils.file_search_service.FileSearchService") as fs, \
                patch("shared.utils.utils.create_model", wraps=create_model) as factory:
            fs.return_value.get_stores_for_agent.return_value = []
            manager.initialize_agent_from_config(row)
        contexts = [c.kwargs["context"] for c in factory.call_args_list]
        self.assertEqual(contexts, [ModelContext("ctx_agent", 3)] * 2)

    def test_the_langgraph_build_passes_the_agents_project(self):
        from shared.utils.langgraph import model_factory
        with patch.object(model_factory, "ChatLiteLLM") as chat:
            model_factory.create_chat_model("openai/x", context=ModelContext("a", 3))
        chat.assert_called_once()
        with self.assertLogs("shared.utils.langgraph.model_factory", "INFO") as logs, \
                patch.object(model_factory, "ChatLiteLLM"):
            model_factory.create_chat_model("openai/x", context=ModelContext("a", 3))
        self.assertIn("agent a, project 3", logs.output[0])


if __name__ == "__main__":
    unittest.main()
