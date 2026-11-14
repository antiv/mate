#!/usr/bin/env python3
"""
Provider keys per workspace, phase 4b of 2.0 (#277, shared/utils/provider_keys.py).

A model call of an agent takes, in this order: the agent's own key or base URL; the
workspace's key for the model's provider; the environment, when the workspace may use
platform keys (the default workspace always may); else it fails. The key is looked up
on each call, on both runtimes, and a workspace key never meets the environment.
"""

import asyncio
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import MagicMock, patch

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils import database_client, provider_keys
from shared.utils.models import AgentConfig, Base, Project, Workspace, WorkspaceProviderKey
from shared.utils.provider_keys import NoProviderKey, model_credentials, provider_of
from shared.utils.utils import ModelContext, create_model

DEFAULT, CLIENT, DEPARTMENT = 1, 2, 3
# Variables a provider's key or endpoint could be read from
PROVIDER_VARIABLES = ("OPENROUTER", "OPENAI", "ANTHROPIC", "GEMINI", "GOOGLE", "DEEPSEEK",
                      "MISTRAL", "GROQ", "XAI", "OLLAMA", "LM_STUDIO")


def environment_without_keys():
    """os.environ with no provider key or endpoint in it."""
    return {k: v for k, v in os.environ.items() if not k.startswith(PROVIDER_VARIABLES)}


class _Workspaces(unittest.TestCase):
    """Default; a client's workspace that may not use platform keys; a department's
    that may. One agent in each."""

    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.addCleanup(engine.dispose)
        self.engine = engine
        self.Session = sessionmaker(bind=engine)
        session = self.Session()
        session.add(Workspace(id=CLIENT, name="Client", slug="client", allow_platform_keys=False))
        session.add(Workspace(id=DEPARTMENT, name="Department", slug="department"))
        session.flush()
        self.projects = {}
        for workspace_id, name in ((DEFAULT, "default"), (CLIENT, "client"), (DEPARTMENT, "department")):
            project = Project(name=f"{name}-project", workspace_id=workspace_id)
            session.add(project)
            session.flush()
            self.projects[workspace_id] = project.id
            session.add(AgentConfig(name=f"{name}_agent", type="llm", model_name="openrouter/x/y",
                                    project_id=project.id))
        session.commit()
        session.close()
        db = MagicMock()
        db.get_session.side_effect = lambda: self.Session()
        patcher = patch.object(database_client, "_database_client", db)
        patcher.start()
        self.addCleanup(patcher.stop)

    def context(self, workspace_id: int) -> ModelContext:
        name = {DEFAULT: "default", CLIENT: "client", DEPARTMENT: "department"}[workspace_id]
        return ModelContext(agent_name=f"{name}_agent", project_id=self.projects[workspace_id])

    def set_key(self, workspace_id: int, provider: str, api_key: str, base_url=None) -> None:
        session = self.Session()
        row = session.query(WorkspaceProviderKey).filter_by(
            workspace_id=workspace_id, provider=provider).first()
        if row is None:
            row = WorkspaceProviderKey(workspace_id=workspace_id, provider=provider)
            session.add(row)
        row.api_key, row.base_url = api_key, base_url
        session.commit()
        session.close()

    def delete_key(self, workspace_id: int, provider: str) -> None:
        session = self.Session()
        session.query(WorkspaceProviderKey).filter_by(workspace_id=workspace_id, provider=provider).delete()
        session.commit()
        session.close()


class TestProviderOf(unittest.TestCase):

    def test_a_model_name_names_its_provider(self):
        for model, provider in (("gemini-2.5-flash", "google"), ("models/gemini-pro", "google"),
                                ("gemini/gemini-2.5-flash", "google"), ("openai/gpt-4o", "openai"),
                                ("openrouter/google/gemini-2.5-flash", "openrouter"),
                                ("anthropic/claude-sonnet-4-5", "anthropic"),
                                ("ollama_chat/llama3.2", "ollama"), ("ollama/llama3.2", "ollama"),
                                ("lm_studio/qwen", "lm_studio"), ("together_ai/x", "together_ai")):
            with self.subTest(model=model):
                self.assertEqual(provider_of(model), provider)


class TestTheOrder(_Workspaces):

    def test_a_model_for_no_agent_uses_the_environment(self):
        self.assertIsNone(model_credentials(ModelContext(), "openrouter/x/y"))

    def test_the_default_workspace_uses_the_environment_without_a_key_of_its_own(self):
        self.assertIsNone(model_credentials(self.context(DEFAULT), "openrouter/x/y"))
        # Even when its flag says otherwise: it holds what 1.x had
        session = self.Session()
        session.query(Workspace).filter_by(id=DEFAULT).update({"allow_platform_keys": False})
        session.commit()
        session.close()
        self.assertIsNone(model_credentials(self.context(DEFAULT), "openrouter/x/y"))

    def test_a_workspace_that_may_use_platform_keys_uses_the_environment(self):
        self.assertIsNone(model_credentials(self.context(DEPARTMENT), "openrouter/x/y"))

    def test_a_workspace_that_may_not_is_refused(self):
        with self.assertRaises(NoProviderKey) as refused:
            model_credentials(self.context(CLIENT), "openrouter/x/y")
        self.assertEqual(str(refused.exception), "This workspace has no key for openrouter")
        # A provider no workspace can hold a key for, too
        with self.assertRaises(NoProviderKey):
            model_credentials(self.context(CLIENT), "lm_studio/qwen")

    def test_a_workspace_key_wins_over_the_environment(self):
        for workspace_id in (DEFAULT, CLIENT, DEPARTMENT):
            self.set_key(workspace_id, "openrouter", f"sk-or-{workspace_id}")
            with self.subTest(workspace=workspace_id):
                credentials = model_credentials(self.context(workspace_id), "openrouter/x/y")
                self.assertEqual((credentials.api_key, credentials.base_url, credentials.workspace_id),
                                 (f"sk-or-{workspace_id}", "https://openrouter.ai/api/v1", workspace_id))
                self.assertFalse(credentials.own_endpoint)

    def test_the_key_is_for_the_model_s_provider_only(self):
        self.set_key(CLIENT, "openai", "sk-openai")
        with self.assertRaises(NoProviderKey):
            model_credentials(self.context(CLIENT), "openrouter/x/y")
        self.assertEqual(model_credentials(self.context(CLIENT), "openai/gpt-4o").api_key, "sk-openai")

    def test_a_workspace_names_its_own_endpoint(self):
        self.set_key(CLIENT, "openai", "sk-proxy", base_url="https://llm.client.example/v1")
        credentials = model_credentials(self.context(CLIENT), "openai/gpt-4o")
        self.assertEqual(credentials.base_url, "https://llm.client.example/v1")
        self.assertTrue(credentials.own_endpoint)

    def test_a_local_model_needs_the_workspace_s_endpoint(self):
        self.set_key(CLIENT, "ollama", "")
        with self.assertRaises(NoProviderKey) as refused:
            model_credentials(self.context(CLIENT), "ollama_chat/llama3.2")
        self.assertIn("no base URL", str(refused.exception))
        self.set_key(CLIENT, "ollama", "", base_url="https://ollama.client.example")
        self.assertEqual(model_credentials(self.context(CLIENT), "ollama_chat/llama3.2").base_url,
                         "https://ollama.client.example")

    def test_a_key_that_can_t_be_read_is_refused_not_replaced_by_the_environment(self):
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO workspace_provider_keys (workspace_id, provider, api_key, "
                              "updated_at) VALUES (3, 'openrouter', 'enc:v1:not-a-token', CURRENT_TIMESTAMP)"))
        with self.assertLogs("shared.utils.secret_box", "ERROR"), self.assertRaises(NoProviderKey) as refused:
            model_credentials(self.context(DEPARTMENT), "openrouter/x/y")
        self.assertIn("can't be read", str(refused.exception))

    def test_an_agent_with_no_config_belongs_to_the_default_workspace(self):
        self.assertIsNone(model_credentials(ModelContext(agent_name="hardcoded_agent"), "openrouter/x/y"))


class _FakeProvider:
    """An OpenAI-compatible endpoint on 127.0.0.1 that records each call's key."""

    def __init__(self, test: unittest.TestCase):
        self.keys = []
        calls = self.keys

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                calls.append(self.headers.get("Authorization"))
                if request.get("stream"):
                    chunk = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": "m",
                             "choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"},
                                          "finish_reason": "stop"}]}
                    body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
                    content_type = "text/event-stream"
                else:
                    body = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                                       "choices": [{"index": 0, "finish_reason": "stop", "message": {
                                           "role": "assistant", "content": "ok"}}],
                                       "usage": {"prompt_tokens": 1, "completion_tokens": 1,
                                                 "total_tokens": 2}}).encode()
                    content_type = "application/json"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        test.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_port}/v1"


class TestEachCall(_Workspaces):
    """Both runtimes' models, called over HTTP, with the environment cleared."""

    def setUp(self):
        super().setUp()
        self.provider = _FakeProvider(self)
        env = environment_without_keys()
        # The fake provider is on 127.0.0.1, which the outbound guard refuses outside
        # the default workspace
        env["OUTBOUND_ALLOW_PRIVATE_NETWORK"] = "true"
        patcher = patch.dict(os.environ, env, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def ask_adk(self, model) -> str:
        async def run():
            response = await model.llm_client.acompletion(
                model=model.model, messages=[{"role": "user", "content": "q"}], tools=None,
                num_retries=0, **model._additional_args)
            return response.choices[0].message.content
        return asyncio.run(run())

    def ask_langgraph(self, chat) -> str:
        return asyncio.run(chat.ainvoke("q")).content

    def test_an_agent_s_calls_carry_the_workspace_s_key_on_both_runtimes(self):
        from shared.utils.langgraph.model_factory import create_chat_model
        self.set_key(CLIENT, "openai", "sk-client", base_url=self.provider.url)
        model = create_model("openai/m", context=self.context(CLIENT))
        chat = create_chat_model("openai/m", context=self.context(CLIENT))
        self.assertEqual(self.ask_adk(model), "ok")
        self.assertEqual(self.ask_langgraph(chat), "ok")
        self.assertEqual(self.provider.keys, ["Bearer sk-client", "Bearer sk-client"])

    def test_a_changed_or_deleted_key_applies_to_the_next_call(self):
        self.set_key(CLIENT, "openai", "sk-first", base_url=self.provider.url)
        model = create_model("openai/m", context=self.context(CLIENT))
        self.ask_adk(model)
        self.set_key(CLIENT, "openai", "sk-second", base_url=self.provider.url)
        self.ask_adk(model)
        self.assertEqual(self.provider.keys, ["Bearer sk-first", "Bearer sk-second"])
        self.delete_key(CLIENT, "openai")
        with self.assertRaises(NoProviderKey):
            self.ask_adk(model)
        self.assertEqual(len(self.provider.keys), 2)

    def test_the_environment_s_key_is_not_sent_with_a_workspace_key(self):
        # Built while the environment holds the platform's key and endpoint
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-platform",
                                     "OPENAI_BASE_URL": "https://platform.example/v1"}):
            model = create_model("openai/m", context=self.context(CLIENT))
        self.set_key(CLIENT, "openai", "sk-client", base_url=self.provider.url)
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-platform",
                                     "OPENAI_BASE_URL": "https://platform.example/v1"}):
            self.ask_adk(model)
        self.assertEqual(self.provider.keys, ["Bearer sk-client"])

    def test_a_workspace_s_own_endpoint_is_kept_off_the_server_s_network(self):
        # As an agent's own base URL: outside the default workspace, 127.0.0.1 is refused
        self.set_key(CLIENT, "openai", "sk-client", base_url=self.provider.url)
        model = create_model("openai/m", context=self.context(CLIENT))
        with patch.dict(os.environ, {"OUTBOUND_ALLOW_PRIVATE_NETWORK": "false"}):
            with self.assertRaises(Exception):
                self.ask_adk(model)
            self.assertEqual(self.provider.keys, [])
            # The default workspace's isn't
            self.set_key(DEFAULT, "openai", "sk-default", base_url=self.provider.url)
            self.ask_adk(create_model("openai/m", context=self.context(DEFAULT)))
        self.assertEqual(self.provider.keys, ["Bearer sk-default"])

    def test_a_workspace_without_a_key_makes_no_call(self):
        model = create_model("openai/m", context=self.context(CLIENT))
        with self.assertRaises(NoProviderKey):
            self.ask_adk(model)
        self.assertEqual(self.provider.keys, [])

    def test_an_agent_s_own_endpoint_is_left_alone(self):
        model = create_model("openai/m", api_key="sk-agent", base_url=self.provider.url,
                             context=self.context(CLIENT))
        self.assertNotIsInstance(model.llm_client, provider_keys.ProviderKeyCompletions)
        self.assertEqual(self.ask_adk(model), "ok")
        self.assertEqual(self.provider.keys, ["Bearer sk-agent"])

    def test_the_fallback_model_and_a_subagent_s_take_the_workspace_s_key(self):
        from shared.callbacks.model_fallback_callback import make_model_fallback_callback
        from shared.utils.tools import subagent_delegation_tool
        built = []

        def build(*args, **kwargs):
            built.append(create_model(*args, **kwargs))
            return built[-1]

        with patch("shared.utils.utils.create_model", side_effect=build):
            make_model_fallback_callback("client_agent", "openai/x", "openai/m",
                                         project_id=self.projects[CLIENT])
        # A subagent on another model than its parent's
        model, _ = subagent_delegation_tool._resolve_subagent_model(
            "openai/m", None, {"name": "client_agent", "project_id": self.projects[CLIENT],
                               "model_name": "openrouter/x/y"})
        self.set_key(CLIENT, "openai", "sk-client", base_url=self.provider.url)
        for each in (built[0], model):
            self.assertIsInstance(each.llm_client, provider_keys.ProviderKeyCompletions)
            self.ask_adk(each)
        self.assertEqual(self.provider.keys, ["Bearer sk-client"] * 2)

    def test_the_compaction_summarizer_works_for_its_root_agent(self):
        from shared.utils import utils
        if not utils.CONTEXT_COMPACTION_AVAILABLE:
            self.skipTest("ADK has no context compaction")
        root = MagicMock()
        root.name = "client_agent"
        with patch.object(utils, "create_model", wraps=create_model) as factory, \
                patch.object(utils, "App", MagicMock()), \
                patch.object(utils, "LlmEventSummarizer", MagicMock()), \
                patch.object(utils, "EventsCompactionConfig", MagicMock()):
            utils.create_app_with_context_caching(root_agent=root, app_name="client_agent",
                                                  enable_compaction=True,
                                                  compaction_summarizer_model="openai/m")
        self.assertEqual(factory.call_args.kwargs["context"], ModelContext(agent_name="client_agent"))
        # The root agent's workspace is found from its name
        self.assertIsNone(model_credentials(ModelContext(agent_name="department_agent"), "openai/m"))
        with self.assertRaises(NoProviderKey):
            model_credentials(ModelContext(agent_name="client_agent"), "openai/m")


if __name__ == "__main__":
    unittest.main()
