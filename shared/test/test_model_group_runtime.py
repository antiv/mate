#!/usr/bin/env python3
"""
An agent on a model group (shared/utils/model_groups.py): which model its call goes to,
on the workspace's keys; the next ones on an outage, on both runtimes; what usage
records; what an agent may be stored with.
"""

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, patch

import litellm

from shared.test.test_cross_workspace import A, A_ROOT, B, B_ROOT, _platform_admin, _TwoWorkspaces
from shared.test.test_model_groups import _addresses
from shared.utils.model_groups import ModelGroupError, answering_model, leaves_own_servers, usable_models
from shared.utils.models import AgentConfig, AgentConfigVersion, ModelGroup, Workspace, WorkspaceProviderKey
from shared.utils.provider_keys import NoProviderKey
from shared.utils.utils import ModelContext, create_model

KEYS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "DEEPSEEK_API_KEY", "GOOGLE_API_KEY",
        "GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "OLLAMA_API_BASE", "OPENAI_BASE_URL", "OPENAI_API_BASE")


def _down() -> Exception:
    return litellm.InternalServerError("provider down", llm_provider="openai", model="m")


class _Groups(_TwoWorkspaces):
    """Workspace B has a key of its own for openrouter (_TwoWorkspaces)."""

    def setUp(self) -> None:
        super().setUp()
        patcher = patch.dict(os.environ, {})
        patcher.start()
        self.addCleanup(patcher.stop)
        for key in KEYS:
            os.environ.pop(key, None)
        self.a = ModelContext(A_ROOT, self.ids["project_a"])
        self.b = ModelContext(B_ROOT, self.ids["project_b"])

    def _db(self, change: Any) -> Any:
        session = self.Session()
        try:
            result = change(session)
            session.commit()
            return result
        finally:
            session.close()

    def _group(self, name: str, *models: str, **flags: Any) -> None:
        self._db(lambda s: s.add(ModelGroup(
            name=name, label=name, models=json.dumps([{"model": m, "confirmed": []} for m in models]), **flags)))

    def _no_platform_keys(self) -> None:
        self._db(lambda s: s.query(Workspace).filter_by(id=B).update({"allow_platform_keys": False}))


class TestUsableModels(_Groups):

    def test_those_the_installation_has_a_key_for_in_order(self) -> None:
        self._group("g", "anthropic/claude-opus-5", "openai/gpt-4o", "gemini-2.5-flash", "ollama_chat/qwen3:8b")
        os.environ["OPENAI_API_KEY"] = "k"
        self.assertEqual(usable_models("g", self.a), ["openai/gpt-4o", "ollama_chat/qwen3:8b"])
        os.environ["GOOGLE_API_KEY"] = os.environ["ANTHROPIC_API_KEY"] = "k"
        self.assertEqual(usable_models("g", self.a), ["anthropic/claude-opus-5", "openai/gpt-4o",
                                                      "gemini-2.5-flash", "ollama_chat/qwen3:8b"])

    def test_a_workspace_goes_by_its_own_keys(self) -> None:
        self._group("g", "openai/gpt-4o", "openrouter/x/y")
        os.environ["OPENAI_API_KEY"] = "k"
        self.assertEqual(usable_models("g", self.a), ["openai/gpt-4o"])
        self.assertEqual(usable_models("g", self.b), ["openai/gpt-4o", "openrouter/x/y"])
        self._no_platform_keys()
        self.assertEqual(usable_models("g", self.b), ["openrouter/x/y"])

    def test_no_key_for_any(self) -> None:
        self._group("g", "openai/gpt-4o")
        with self.assertRaisesRegex(NoProviderKey, "no key for any model of group/g"):
            usable_models("g", self.a)
        os.environ["OPENAI_API_KEY"] = "k"
        self._no_platform_keys()
        with self.assertRaises(NoProviderKey):
            usable_models("g", self.b)

    def test_a_group_that_is_gone_or_empty_names_itself(self) -> None:
        self._group("empty")
        for name in ("empty", "gone"):
            with self.assertRaisesRegex(ModelGroupError, f"group/{name}"):
                usable_models(name, self.a)

    def test_own_servers_only_is_checked_as_the_call_is_sent(self) -> None:
        self._group("local", "ollama_chat/glm:cloud", "openai/qwen", "ollama_chat/qwen3:8b", own_servers_only=True)
        os.environ.update(OPENAI_API_KEY="k", OLLAMA_API_BASE="http://ollama:11434")
        with _addresses(ollama="10.0.0.7"):
            self.assertEqual(usable_models("local", self.a), ["ollama_chat/qwen3:8b"])
            os.environ["OPENAI_BASE_URL"] = "http://10.0.0.8/v1"
            self.assertEqual(usable_models("local", self.a), ["openai/qwen", "ollama_chat/qwen3:8b"])
        # The server moved out of the network since the group was saved
        with _addresses(ollama="93.184.216.34"), self.assertRaises(NoProviderKey):
            os.environ["OPENAI_BASE_URL"] = "https://93.184.216.34/v1"
            usable_models("local", self.a)

    def test_a_workspace_s_own_entry_doesn_t_take_it_out(self) -> None:
        self._group("local", "ollama_chat/qwen3:8b", "openai/qwen", own_servers_only=True)
        os.environ.update(OPENAI_BASE_URL="http://10.0.0.8/v1", OPENAI_API_KEY="k")
        for provider, url in (("ollama", "https://93.184.216.34"), ("openai", None)):
            self._db(lambda s: s.add(WorkspaceProviderKey(workspace_id=B, provider=provider, api_key="k", base_url=url)))
        with _addresses(**{"api__openai__com": "104.18.0.1"}):
            with self.assertRaisesRegex(NoProviderKey, "own servers"):
                usable_models("local", self.b)
            self._db(lambda s: s.query(WorkspaceProviderKey).filter_by(provider="ollama").update(
                {"base_url": "http://10.0.0.9:11434"}))
            self.assertEqual(usable_models("local", self.b), ["ollama_chat/qwen3:8b"])

    def test_the_model_that_answers_now(self) -> None:
        self._group("g", "openai/gpt-4o")
        self.assertEqual(answering_model("group/g", self.a), "")
        os.environ["OPENAI_API_KEY"] = "k"
        self.assertEqual(answering_model("group/g", self.a), "openai/gpt-4o")
        self.assertEqual(answering_model("deepseek/x", self.a), "deepseek/x")
        self.assertEqual(answering_model(None, self.a), "")
        self.assertEqual(answering_model("group/gone", self.a), "")

    def test_a_sub_agent_stays_where_its_parent_s_group_keeps_it(self) -> None:
        self._group("local", "ollama/x", own_servers_only=True)
        self._group("local-two", "ollama/x", own_servers_only=True)
        self._group("g", "openai/gpt-4o")
        for model in ("group/local", "group/local-two"):
            self.assertIsNone(leaves_own_servers("group/local", model))
        for model in ("openai/gpt-4o", "group/g", "ollama/x", "gemini-2.5-flash"):
            self.assertIn("stays on its own servers", leaves_own_servers("group/local", model))
            self.assertIsNone(leaves_own_servers("group/g", model))
            self.assertIsNone(leaves_own_servers("openai/x", model))
        from shared.utils.tools.subagent_delegation_tool import _resolve_subagent_model
        parent = {"name": A_ROOT, "model_name": "group/local", "project_id": self.ids["project_a"]}
        for task, default in (("openai/gpt-4o", None), (None, "openai/gpt-4o")):
            with self.assertRaises(ValueError):
                _resolve_subagent_model(task, default, parent)
        self.assertEqual(_resolve_subagent_model(None, None, parent)[1], "group/local")


class TestAdk(_Groups):
    """An agent built as the agent server builds one, its models answering from LiteLLM's
    mock_response."""

    def _agent(self, **row: Any) -> Any:
        from shared.utils.agent_manager import AgentManager
        config = AgentConfig(**{"name": "support", "type": "llm", "instruction": "hi", "model_name": "group/g",
                                "project_id": self.ids["project_a"], **row})
        with patch.dict(os.environ, {"MATE_PLUGINS_ENABLED": "false"}), \
                patch("shared.utils.file_search_service.FileSearchService") as stores:
            stores.return_value.get_stores_for_agent.return_value = []
            return AgentManager().initialize_agent_from_config(config)

    def _run(self, agent: Any, replies: Dict[str, Any], turns: int = 1) -> Dict[str, Any]:
        """Texts, the recorded rows and the requests; replies by the name LiteLLM is called with."""
        from google.adk.runners import InMemoryRunner
        from google.genai import types
        requests: List[Dict[str, Any]] = []
        rows: List[Dict[str, Any]] = []

        async def acompletion(*args: Any, **kwargs: Any) -> Any:
            requests.append(kwargs)
            reply = replies[kwargs["model"]]
            if callable(reply):
                reply = reply(kwargs)
            if isinstance(reply, Exception):
                raise reply
            message = reply if isinstance(reply, dict) else {"role": "assistant", "content": reply}
            return litellm.ModelResponse(model=kwargs["model"], choices=[{"index": 0, "finish_reason": "stop",
                                                                         "message": message}],
                                         usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})

        service = MagicMock()
        service.is_connected.return_value = True
        service.insert_token_usage.side_effect = lambda row: rows.append(row) or {"id": 1}
        service.log_token_usage.side_effect = lambda **row: rows.append(row)

        async def go() -> List[str]:
            runner = InMemoryRunner(agent=agent, app_name="app")
            session = await runner.session_service.create_session(app_name="app", user_id="u1")
            texts = []
            for _ in range(turns):
                async for event in runner.run_async(user_id="u1", session_id=session.id, new_message=types.Content(
                        role="user", parts=[types.Part(text="hi")])):
                    texts += [p.text for p in (event.content.parts if event.content else []) or [] if p.text]
            return texts

        with patch("shared.callbacks.rbac_callback.rbac_before_model_callback", return_value=None), \
                patch("google.adk.models.lite_llm.LiteLLMClient.acompletion",
                      lambda _self, model, messages, tools, **kw: acompletion(
                          model=model, messages=messages, tools=tools, **kw)), \
                patch("shared.callbacks.token_usage_callback.get_token_usage_service", return_value=service), \
                patch("shared.callbacks.error_callback.get_token_usage_service", return_value=service), \
                patch("shared.utils.audit_service.log") as audit:
            texts = asyncio.run(go())
        return {"texts": texts, "rows": [(r["model_name"], r.get("model_group"), r.get("status", "SUCCESS"),
                                          bool(r.get("is_fallback"))) for r in rows],
                "requests": requests, "audit": audit}

    def _keys(self) -> None:
        os.environ.update(OPENAI_API_KEY="k", ANTHROPIC_API_KEY="k", DEEPSEEK_API_KEY="k")

    def test_the_first_model_answers_and_is_recorded_with_the_group(self) -> None:
        self._keys()
        self._group("g", "openai/one", "anthropic/two")
        ran = self._run(self._agent(), {"openai/one": "one", "anthropic/two": "two"})
        self.assertEqual((ran["texts"], ran["rows"]), (["one"], [("openai/one", "g", "SUCCESS", False)]))
        ran["audit"].assert_not_called()

    def test_the_next_ones_on_an_outage(self) -> None:
        self._keys()
        self._group("g", "openai/one", "anthropic/two", "deepseek/three")
        ran = self._run(self._agent(), {"openai/one": _down(), "anthropic/two": _down(), "deepseek/three": "three"})
        self.assertEqual(ran["texts"], ["three"])
        self.assertEqual(ran["rows"], [("openai/one", "g", "ERROR", False), ("deepseek/three", "g", "SUCCESS", True)])
        self.assertEqual(ran["audit"].call_args.kwargs["details"]["primary_model"], "openai/one")
        self.assertEqual(ran["audit"].call_args.kwargs["details"]["fallback_model"], "deepseek/three")

    def test_not_on_what_the_request_is_at_fault_for(self) -> None:
        self._keys()
        self._group("g", "openai/one", "anthropic/two", "deepseek/three")
        refused = litellm.ContentPolicyViolationError("refused", model="m", llm_provider="openai")
        with self.assertRaises(litellm.ContentPolicyViolationError):
            self._run(self._agent(), {"openai/one": refused, "anthropic/two": "two"})
        # Nor when the next one refuses: the first one's error stands
        with self.assertRaises(litellm.InternalServerError):
            self._run(self._agent(), {"openai/one": _down(), "anthropic/two": refused, "deepseek/three": "three"})

    def test_the_list_takes_the_place_of_a_fallback_model(self) -> None:
        self._keys()
        self._group("g", "openai/one")
        with self.assertRaises(litellm.InternalServerError):
            self._run(self._agent(fallback_model="anthropic/two"),
                      {"openai/one": _down(), "anthropic/two": "two"})

    def test_a_changed_group_and_key_apply_to_a_running_agent(self) -> None:
        os.environ["OPENAI_API_KEY"] = "k"
        self._group("g", "openai/one", "anthropic/two")
        agent = self._agent()
        replies = {"openai/one": "one", "anthropic/two": "two"}
        self.assertEqual(self._run(agent, replies)["texts"], ["one"])
        del os.environ["OPENAI_API_KEY"]
        os.environ["ANTHROPIC_API_KEY"] = "k"
        self.assertEqual(self._run(agent, replies)["texts"], ["two"])
        os.environ["OPENAI_API_KEY"] = "k"
        self._db(lambda s: s.query(ModelGroup).update({"models": '[{"model": "openai/one", "confirmed": []}]'}))
        self.assertEqual(self._run(agent, replies)["rows"], [("openai/one", "g", "SUCCESS", False)])
        self._db(lambda s: s.query(ModelGroup).delete())
        with self.assertRaisesRegex(ModelGroupError, "group/g"):
            self._run(agent, replies)

    def test_tool_calls_keep_the_ids_that_pair_them(self) -> None:
        self._keys()
        self._group("g", "openai/one")
        agent = self._agent()

        def lookup() -> str:
            """Looks it up."""
            return "found"
        agent.tools = [lookup]
        call = {"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "lookup", "arguments": "{}"}}]}
        ran = self._run(agent, {"openai/one": lambda kw: "done" if len(kw["messages"]) > 2 else call})
        self.assertEqual(ran["texts"], ["done"])
        tool = next(m for m in ran["requests"][-1]["messages"] if m["role"] == "tool")
        self.assertEqual(tool["tool_call_id"], "call_1")
        self.assertEqual(len(ran["rows"]), 2)

    def test_gemini_s_own_backend_gets_no_ids_of_adk(self) -> None:
        from google.adk.models.google_llm import Gemini
        from google.adk.models.llm_request import LlmRequest
        from google.adk.models.llm_response import LlmResponse
        from google.genai import types
        os.environ["GOOGLE_API_KEY"] = "k"
        self._group("g", "gemini-2.5-flash")
        seen = {}

        async def generate(_self: Any, request: Any, stream: bool = False) -> Any:
            seen.update(model=request.model, id=request.contents[0].parts[0].function_call.id)
            yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text="ok")]))
        request = LlmRequest(model="group/g", contents=[types.Content(role="model", parts=[types.Part(
            function_call=types.FunctionCall(id="adk-1", name="f", args={}))])])

        async def go() -> list:
            return [r async for r in create_model("group/g", context=self.a).generate_content_async(request)]
        with patch.object(Gemini, "generate_content_async", generate):
            self.assertEqual(len(asyncio.run(go())), 1)
        self.assertEqual(seen, {"model": "gemini-2.5-flash", "id": None})
        self.assertEqual(request.contents[0].parts[0].function_call.id, "adk-1")

    def test_an_output_schema_beside_tools_goes_through_a_tool(self) -> None:
        self.assertFalse(create_model("group/g", context=self.a).capabilities.output_schema_and_tools)

    def test_an_endpoint_of_its_own_is_refused(self) -> None:
        for endpoint in ({"base_url": "http://x"}, {"api_key": "k"}):
            with self.assertRaisesRegex(ValueError, "group/g"):
                create_model("group/g", context=self.a, **endpoint)


class TestLangGraph(_Groups):

    def _build(self, **config: Any) -> Any:
        from shared.utils.langgraph.agent_builder import AgentBuilder
        config = {"name": "support", "type": "llm", "model_name": "group/g", "instruction": "Be brief.",
                  "project_id": self.ids["project_a"], **config}
        with patch("shared.utils.langgraph.agent_builder._load_child_configs", return_value=[]), \
                patch.object(AgentBuilder, "_build_tools", return_value=[self._lookup]):
            return asyncio.run(AgentBuilder().build_for_config(config))

    @staticmethod
    def _lookup(order: str) -> str:
        """Look up an order."""
        return "shipped"

    def _ask(self, built: Any, replies: Dict[str, Any]) -> Dict[str, Any]:
        from langchain_litellm import ChatLiteLLM
        from shared.utils.langgraph.event_translator import translate_stream
        from shared.utils.langgraph.executor import _log_token_usage

        def chat_model(name: str, **_: Any) -> Any:
            return ChatLiteLLM(model=name, streaming=True, api_key="x", model_kwargs={"mock_response": replies[name]})

        async def go() -> list:
            stream = built.graph.astream({"messages": [("user", "hi")]}, stream_mode=["messages", "updates"],
                                         subgraphs=True)
            return [event async for event, complete in translate_stream(stream, "support", "inv1") if complete]

        service = MagicMock()
        with patch("shared.utils.langgraph.model_factory.create_chat_model", side_effect=chat_model), \
                patch("shared.utils.token_usage_service.get_token_usage_service", return_value=service), \
                patch("shared.utils.audit_service.log") as audit:
            event = asyncio.run(go())[-1]
            event.setdefault("usageMetadata", {"prompt_token_count": 3, "candidates_token_count": 2})
            _log_token_usage(event, "support", "u1", "s1", built.model_names, built.fallback_models,
                             built.group_models)
        row = service.log_token_usage.call_args.kwargs
        return {"text": event["content"]["parts"][0]["text"], "audit": audit,
                "row": (row["model_name"], row["model_group"], row["is_fallback"])}

    def test_the_first_answers_the_next_on_an_outage_and_a_change_applies(self) -> None:
        os.environ.update(OPENAI_API_KEY="k", ANTHROPIC_API_KEY="k", DEEPSEEK_API_KEY="k")
        self._group("g", "openai/one", "anthropic/two", "deepseek/three")
        built = self._build(fallback_model="xai/other")
        self.assertEqual(built.fallback_models, {})
        ran = self._ask(built, {"openai/one": "one", "anthropic/two": "two", "deepseek/three": "three"})
        self.assertEqual((ran["text"], ran["row"]), ("one", ("openai/one", "g", False)))
        ran["audit"].assert_not_called()

        built = self._build()
        ran = self._ask(built, {"openai/one": _down(), "anthropic/two": _down(), "deepseek/three": "three"})
        self.assertEqual((ran["text"], ran["row"]), ("three", ("deepseek/three", "g", True)))
        self.assertEqual(ran["audit"].call_args.kwargs["details"]["primary_model"], "openai/one")

        self._db(lambda s: s.query(ModelGroup).update({"models": '[{"model": "xai/new", "confirmed": []}]'}))
        os.environ["XAI_API_KEY"] = "k"
        ran = self._ask(built, {"xai/new": "new"})
        self.assertEqual((ran["text"], ran["row"]), ("new", ("xai/new", "g", False)))
        self._db(lambda s: s.query(ModelGroup).delete())
        with self.assertRaisesRegex(ModelGroupError, "group/g"):
            self._ask(built, {})

    def test_the_names_litellm_is_called_with_are_told_back(self) -> None:
        os.environ.update(GOOGLE_API_KEY="k")
        self._group("g", "gemini-2.5-flash", "lm_studio/qwen")
        built = self._build()
        ran = self._ask(built, {"gemini-2.5-flash": _down(), "lm_studio/qwen": "ok"})
        self.assertEqual(ran["text"], "ok")
        group = built.group_models["support"]
        self.assertEqual(group.answered("gemini/gemini-2.5-flash"), ("gemini-2.5-flash", "gemini-2.5-flash"))
        self.assertEqual(group.answered("openai/qwen"), ("lm_studio/qwen", "gemini-2.5-flash"))

    def test_an_endpoint_of_its_own_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "group/g"):
            self._build(model_base_url="http://127.0.0.1:1/v1")


class TestSaving(_Groups):

    def setUp(self) -> None:
        super().setUp()
        self._group("g", "openai/gpt-4o")
        self._group("empty")
        self.config = self._db(lambda s: s.query(AgentConfig.id).filter_by(name=A_ROOT).scalar())

    def _form(self, **values: Any) -> Dict[str, Any]:
        return {"name": A_ROOT, "type": "llm", "project_id": str(self.ids["project_a"]), "description": "d",
                "instruction": "i", **values}

    def _put(self, **values: Any) -> Any:
        with patch("httpx.AsyncClient.post"):
            return self.client.put(f"/dashboard/api/agents/{self.config}", headers=_platform_admin(A),
                                   data=self._form(**values))

    def _model(self, name: str = A_ROOT) -> Optional[str]:
        return self._db(lambda s: s.query(AgentConfig.model_name).filter_by(name=name).scalar())

    BAD = ({"model_name": "group/gone"}, {"model_name": "group/empty"},
           {"model_name": "group/g", "model_base_url": "http://x"}, {"model_name": "group/g", "model_api_key": "k"},
           {"model_name": "openai/x", "fallback_model": "group/g"})

    def test_created_and_updated(self) -> None:
        for bad in self.BAD:
            self.assertEqual(self._put(**bad).status_code, 400, bad)
            created = self.client.post("/dashboard/api/agents", headers=_platform_admin(A),
                                       data=self._form(**bad, name="new_one"))
            self.assertEqual(created.status_code, 400, bad)
        self.assertIsNone(self._model("new_one"))
        self.assertEqual(self._put(model_name="group/g").status_code, 200)
        self.assertEqual(self._model(), "group/g")

    def test_a_key_the_form_never_saw_counts(self) -> None:
        self._db(lambda s: s.query(AgentConfig).filter_by(name=A_ROOT).update({"model_api_key": "k"}))
        from shared.utils.config_secrets import STORED_SECRET_SENTINEL
        self.assertEqual(self._put(model_name="group/g", model_api_key=STORED_SECRET_SENTINEL).status_code, 400)
        self.assertEqual(self._put(model_name="group/g", model_api_key="").status_code, 200)

    def test_rolled_back(self) -> None:
        version = AgentConfigVersion(agent_config_id=self.config, version_number=7)
        version.set_snapshot({"name": A_ROOT, "model_name": "group/gone", "project_id": self.ids["project_a"]})

        def add(session: Any) -> int:
            session.add(version)
            session.flush()
            return version.id
        version_id = self._db(add)
        response = self.client.post(f"/dashboard/api/agents/{self.config}/rollback/{version_id}",
                                    headers=_platform_admin(A))
        self.assertEqual(response.status_code, 400)
        self.assertIn("group/gone", response.json()["detail"])
        self.assertEqual(self._model(), "m")

    def test_imported(self) -> None:
        agents = [{"name": "imported_bad", "type": "llm", "model_name": "group/gone",
                   "project_id": self.ids["project_a"]},
                  {"name": "imported_good", "type": "llm", "model_name": "group/g",
                   "project_id": self.ids["project_a"]}]
        result = self.client.post("/dashboard/api/agents/import", headers=_platform_admin(A),
                                  json={"agents": agents}).json()
        self.assertIn("group/gone", json.dumps(result))
        self.assertIsNone(self._model("imported_bad"))
        self.assertEqual(self._model("imported_good"), "group/g")

    def test_a_template(self) -> None:
        from shared.test.test_cross_workspace import auth_server
        template = {"template_meta": {"id": "t", "agent_prefix": "tpl"}, "project": {"name": "T"},
                    "agents": [{"name": "tpl_root", "type": "llm", "model_name": "group/gone", "instruction": "i"}]}
        with patch.object(auth_server.dashboard_server.template_service, "get_template", return_value=template):
            response = self.client.post("/dashboard/api/templates/import", headers=_platform_admin(A),
                                        json={"template_id": "t", "project_name": "From a template"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("group/gone", response.text)

    def test_by_an_agent(self) -> None:
        from shared.utils.model_groups import save_refusal
        self.assertIsNone(self._db(lambda s: save_refusal(s, {"model_name": "group/g"})))
        self.assertIsNone(self._db(lambda s: save_refusal(s, {"model_name": "openai/x", "model_base_url": "http://x"})))
        # A field the change leaves out is the stored one's
        self.assertIn("Model Base URL", self._db(lambda s: save_refusal(
            s, {"model_name": "group/g"}, {"model_base_url": "http://x"})))
        self.assertIsNone(self._db(lambda s: save_refusal(s, {"description": "d"}, {"model_name": "openai/x"})))


class TestChoosing(_Groups):
    """What the agent editor and Create Agent's tools are offered."""

    def setUp(self) -> None:
        super().setUp()
        self._group("g", "openai/gpt-4o", "openrouter/x/y")
        self._group("local", "ollama_chat/qwen3:8b", own_servers_only=True)
        self._group("bare")
        os.environ["OPENAI_API_KEY"] = "k"

    def _offered(self, headers: Dict[str, str]) -> Dict[str, Dict[str, Any]]:
        response = self.client.get("/dashboard/api/workspace-models/groups", headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        return {g["name"]: g for g in response.json()["groups"]}

    def test_each_with_the_model_that_answers_in_this_workspace(self) -> None:
        offered = self._offered(_platform_admin(A))
        self.assertEqual((offered["g"]["model_name"], offered["g"]["answers"], offered["g"]["reason"]),
                         ("group/g", "openai/gpt-4o", None))
        self.assertEqual((offered["bare"]["has_models"], offered["bare"]["answers"]), (False, None))
        self.assertIn("has no models", offered["bare"]["reason"])
        with _addresses(localhost="127.0.0.1"):
            self.assertEqual(self._offered(_platform_admin(A))["local"]["answers"], "ollama_chat/qwen3:8b")
        # B has a key of its own for openrouter, and no other
        self.assertEqual(self._offered(_platform_admin(B))["g"]["answers"], "openai/gpt-4o")
        self._no_platform_keys()
        offered = self._offered(_platform_admin(B))
        self.assertEqual(offered["g"]["answers"], "openrouter/x/y")
        self.assertEqual((offered["local"]["has_models"], offered["local"]["answers"]), (True, None))
        self.assertIn("own servers", offered["local"]["reason"])

    def test_it_is_the_model_a_call_goes_to(self) -> None:
        for workspace, context in ((A, self.a), (B, self.b)):
            self.assertEqual(self._offered(_platform_admin(workspace))["g"]["answers"], answering_model("group/g", context))

    def test_the_model_of_an_agent_without_one(self) -> None:
        os.environ["GEMINI_MODEL"] = "openrouter/x/default"
        response = self.client.get("/dashboard/api/workspace-models/groups", headers=_platform_admin(A))
        self.assertEqual(response.json()["default_model"], "openrouter/x/default")

    def _tool(self, name: str, *args: Any, agent: str = A_ROOT, admin: bool = True, **kwargs: Any) -> Dict[str, Any]:
        from shared.utils.database_client import get_database_client
        from shared.utils.tools import create_agent_tool
        with patch.object(create_agent_tool, "_is_admin_user", return_value=admin), \
                patch.object(create_agent_tool, "reload_agent_cache", return_value={"success": True}), \
                patch.object(create_agent_tool, "get_database_client", get_database_client):
            return getattr(create_agent_tool, name)(*args, tool_context=MagicMock(agent_name=agent), **kwargs)

    def _stored(self, name: str) -> Optional[str]:
        return self._db(lambda s: s.query(AgentConfig.model_name).filter_by(name=name).scalar())

    def test_an_agent_lists_those_an_agent_can_be_stored_with(self) -> None:
        self._no_platform_keys()
        listed = {agent: {g["model_name"]: g["answers"] for g in self._tool("list_model_groups", agent=agent)["groups"]}
                  for agent in (A_ROOT, B_ROOT)}
        self.assertEqual((listed[A_ROOT]["group/g"], listed[B_ROOT]["group/g"]), ("openai/gpt-4o", "openrouter/x/y"))
        self.assertNotIn("group/bare", listed[A_ROOT])
        self.assertEqual(self._tool("list_model_groups", admin=False)["status"], "error")
        self.assertEqual(self._tool("list_model_groups", agent="nobody_s")["status"], "error")

    def test_it_comes_with_create_agent(self) -> None:
        from shared.utils.tools.tool_factory import ToolFactory
        tools = ToolFactory().create_tools({"name": A_ROOT, "tool_config": json.dumps({"create_agent": True})})
        self.assertIn("list_model_groups", [getattr(t, "__name__", "") for t in tools])

    def test_an_agent_puts_another_on_a_group(self) -> None:
        made = self._tool("create_agent", "child", "d", "i", model="group/g")
        self.assertEqual((made["status"], self._stored("child")), ("success", "group/g"), made)
        self.assertEqual(self._tool("update_agent", "child", model="group/local")["status"], "success")
        self.assertEqual(self._stored("child"), "group/local")
        for bad in ("group/bare", "group/gone"):
            self.assertIn(bad, self._tool("update_agent", "child", model=bad)["error_message"])
            self.assertIn(bad, self._tool("create_agent", "child_two", "d", "i", model=bad)["error_message"])
        self.assertEqual((self._stored("child"), self._stored("child_two")), ("group/local", None))


class TestStandalone(_Groups):

    def _build(self, **bundle: Any) -> Path:
        from build_standalone_agent import create_database
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        with patch("builtins.print"):
            return create_database(Path(folder.name), [{"name": "a", "type": "llm", "model_name": "group/g"}], **bundle)

    def test_the_build_brings_the_groups_its_agents_name(self) -> None:
        from shared.test.test_cross_workspace import auth_server
        self._group("g", "openai/gpt-4o")
        self._group("other", "openai/gpt-4o")
        self._db(lambda s: s.query(AgentConfig).filter_by(name=A_ROOT).update({"model_name": "group/g"}))
        dashboard = auth_server.dashboard_server
        bundle = dashboard._export_agent_configs(root_agent=A_ROOT, project_id=self.ids["project_a"], for_build=True)
        self.assertEqual([g["name"] for g in bundle["model_groups"]], ["g"])
        self.assertNotIn("model_groups", dashboard._export_agent_configs(project_id=self.ids["project_a"]))
        import sqlite3
        db = sqlite3.connect(self._build(model_groups=bundle["model_groups"]))
        self.assertIn("openai/gpt-4o", db.execute("SELECT models FROM model_groups").fetchone()[0])
        db.close()

    def test_a_file_without_them_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "model group"):
            self._build()


if __name__ == "__main__":
    unittest.main()
