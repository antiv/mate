#!/usr/bin/env python3
"""
Tests for the per-agent fallback model.

When an agent's model fails (a provider outage, a timeout), the request is
re-run once on its `fallback_model`, so the person chatting gets an answer
rather than an error. The model that answered is what token usage records, and
a fallback leaves a warning and an audit entry.

The fallback never inherits the agent's model_base_url / model_api_key: those
belong to the primary model, and reusing them would send that key to whatever
host the fallback's provider lives on.
"""

import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import litellm

from shared.callbacks.model_fallback_callback import (
    make_model_fallback_callback,
    record_model_fallback,
)


def _provider_down():
    return litellm.InternalServerError("provider down", llm_provider="openai", model="primary")


def _callback_context(agent_name="support", user_id="u1", session_id="s1"):
    """Minimal stand-in for an ADK CallbackContext."""
    ctx = SimpleNamespace(agent_name=agent_name, state={"current_model_name": "openai/primary"})
    ctx._invocation_context = SimpleNamespace(user_id=user_id, session=SimpleNamespace(id=session_id))
    return ctx


def _lite_llm(model, mock_response):
    from google.adk.models.lite_llm import LiteLlm
    return LiteLlm(model, mock_response=mock_response)


class TestFallbackCallback(unittest.TestCase):

    def _callback(self, fallback_llm):
        with patch("shared.utils.utils.create_model", return_value=fallback_llm) as create:
            callback = make_model_fallback_callback("support", "openai/primary", "anthropic/fallback")
        return callback, create

    def _request(self):
        from google.adk.models.llm_request import LlmRequest
        from google.genai import types
        return LlmRequest(model="openai/primary", contents=[
            types.Content(role="user", parts=[types.Part(text="hi")])])

    def test_the_fallback_answers_and_is_recorded(self):
        callback, _ = self._callback(_lite_llm("anthropic/fallback", "hello from the fallback"))
        ctx = _callback_context()
        with patch("shared.utils.audit_service.log") as audit:
            response = asyncio.run(callback(callback_context=ctx, llm_request=self._request(),
                                            error=_provider_down()))

        self.assertEqual(response.content.parts[0].text, "hello from the fallback")
        # Token logging reads this, so the tokens are booked to the model that answered
        self.assertEqual(ctx.state["current_model_name"], "anthropic/fallback")
        audit.assert_called_once()
        actor, action, _resource = audit.call_args.args
        self.assertEqual((actor, action), ("u1", "agent.model_fallback"))
        details = audit.call_args.kwargs["details"]
        self.assertEqual(details["primary_model"], "openai/primary")
        self.assertEqual(details["fallback_model"], "anthropic/fallback")
        self.assertIn("provider down", details["error"])

    def test_the_failed_request_is_left_unchanged(self):
        callback, _ = self._callback(_lite_llm("anthropic/fallback", "ok"))
        request = self._request()
        with patch("shared.utils.audit_service.log"):
            asyncio.run(callback(callback_context=_callback_context(), llm_request=request,
                                 error=_provider_down()))
        self.assertEqual(request.model, "openai/primary")

    def test_a_failing_fallback_lets_the_original_error_through(self):
        callback, _ = self._callback(_lite_llm("anthropic/fallback", _provider_down()))
        ctx = _callback_context()
        with patch("shared.utils.audit_service.log") as audit:
            response = asyncio.run(callback(callback_context=ctx, llm_request=self._request(),
                                            error=_provider_down()))
        # None makes ADK re-raise the primary's error, as without a fallback
        self.assertIsNone(response)
        self.assertEqual(ctx.state["current_model_name"], "openai/primary")
        audit.assert_not_called()

    def test_the_fallback_does_not_inherit_the_agents_endpoint(self):
        _, create = self._callback(MagicMock())
        create.assert_called_once_with(model_name="anthropic/fallback")


    def test_a_request_error_does_not_fall_back(self):
        # Otherwise anyone chatting could move a request past the primary
        # provider's own filters by making it refuse
        fallback = MagicMock()
        callback, _ = self._callback(fallback)
        for error in (
            litellm.ContentPolicyViolationError("refused", model="primary", llm_provider="openai"),
            litellm.ContextWindowExceededError("too long", model="primary", llm_provider="openai"),
            litellm.BadRequestError("bad", model="primary", llm_provider="openai"),
            litellm.AuthenticationError("bad key", llm_provider="openai", model="primary"),
            ValueError("not a provider error"),
        ):
            with self.subTest(error=type(error).__name__), patch("shared.utils.audit_service.log") as audit:
                response = asyncio.run(callback(callback_context=_callback_context(),
                                                llm_request=self._request(), error=error))
                self.assertIsNone(response)
                audit.assert_not_called()
        fallback.generate_content_async.assert_not_called()

    def test_tools_that_cannot_be_copied_do_not_stop_the_fallback(self):
        import threading
        callback, _ = self._callback(_lite_llm("anthropic/fallback", "ok"))
        request = self._request()
        request.tools_dict["holds_a_lock"] = SimpleNamespace(lock=threading.Lock())
        with patch("shared.utils.audit_service.log"):
            response = asyncio.run(callback(callback_context=_callback_context(),
                                            llm_request=request, error=_provider_down()))
        self.assertEqual(response.content.parts[0].text, "ok")


class TestIsProviderOutage(unittest.TestCase):

    def test_outages(self):
        from google.genai.errors import ClientError, ServerError
        from shared.callbacks.model_fallback_callback import is_provider_outage
        for error in (
            _provider_down(),
            litellm.Timeout("slow", model="primary", llm_provider="openai"),
            litellm.APIConnectionError("no route", llm_provider="openai", model="primary"),
            litellm.RateLimitError("429", llm_provider="openai", model="primary"),
            litellm.ServiceUnavailableError("503", llm_provider="openai", model="primary"),
            litellm.BadGatewayError("502", llm_provider="openai", model="primary"),
            TimeoutError(),
            ConnectionResetError(),
            ServerError(503, {"error": {"message": "unavailable"}}),
            ClientError(429, {"error": {"message": "resource exhausted"}}),
        ):
            with self.subTest(error=type(error).__name__):
                self.assertTrue(is_provider_outage(error))

    def test_not_outages(self):
        from google.genai.errors import ClientError
        from shared.callbacks.model_fallback_callback import is_provider_outage
        for error in (
            litellm.ContentPolicyViolationError("refused", model="primary", llm_provider="openai"),
            litellm.ContextWindowExceededError("too long", model="primary", llm_provider="openai"),
            ClientError(400, {"error": {"message": "bad request"}}),
            ValueError("x"),
        ):
            with self.subTest(error=type(error).__name__):
                self.assertFalse(is_provider_outage(error))


class TestRecordModelFallback(unittest.TestCase):

    def test_audit_failure_is_swallowed(self):
        with patch("shared.utils.audit_service.log", side_effect=RuntimeError("db down")):
            record_model_fallback("a", "u1", "openai/primary", "anthropic/fallback", None)

    def test_warns(self):
        with patch("shared.utils.audit_service.log"), \
                self.assertLogs("shared.callbacks.model_fallback_callback", level="WARNING") as logs:
            record_model_fallback("a", "u1", "openai/primary", "anthropic/fallback", _provider_down())
        self.assertIn("anthropic/fallback", logs.output[0])


class TestAdkRun(unittest.TestCase):
    """End to end through ADK: the caller gets the fallback's answer, not an error."""

    def _run(self, agent):
        from google.adk.runners import InMemoryRunner
        from google.genai import types

        async def go():
            runner = InMemoryRunner(agent=agent, app_name="app")
            session = await runner.session_service.create_session(app_name="app", user_id="u1")
            texts = []
            async for event in runner.run_async(
                    user_id="u1", session_id=session.id,
                    new_message=types.Content(role="user", parts=[types.Part(text="hi")])):
                for part in (event.content.parts if event.content else []) or []:
                    if part.text:
                        texts.append(part.text)
            return texts
        return asyncio.run(go())

    def test_a_failing_model_is_answered_by_the_fallback(self):
        from google.adk.agents import Agent

        with patch("shared.utils.utils.create_model",
                   return_value=_lite_llm("anthropic/fallback", "hello from the fallback")):
            fallback_callback = make_model_fallback_callback("support", "openai/primary",
                                                             "anthropic/fallback")
        seen = {}

        def after_model(callback_context, llm_response):
            # Guardrails and token logging run here, on the fallback's answer too
            seen["model"] = callback_context.state.get("current_model_name")
            seen["text"] = llm_response.content.parts[0].text

        agent = Agent(name="support", model=_lite_llm("openai/primary", _provider_down()),
                      instruction="Be brief.", on_model_error_callback=[fallback_callback],
                      after_model_callback=after_model)
        with patch("shared.utils.audit_service.log"):
            texts = self._run(agent)

        self.assertEqual(texts, ["hello from the fallback"])
        self.assertEqual(seen, {"model": "anthropic/fallback", "text": "hello from the fallback"})


class TestAdkAgentWiring(unittest.TestCase):

    def _build(self, **row):
        from shared.utils.models import AgentConfig
        with patch("shared.utils.agent_manager.get_database_client", return_value=MagicMock()):
            from shared.utils.agent_manager import AgentManager
            manager = AgentManager()
        config = AgentConfig(name="support", type="llm", instruction="hi", **row)
        with patch("shared.utils.file_search_service.FileSearchService") as fs:
            fs.return_value.get_stores_for_agent.return_value = []
            return manager.initialize_agent_from_config(config)

    def _callback_names(self, agent):
        callbacks = agent.on_model_error_callback or []
        if not isinstance(callbacks, list):
            callbacks = [callbacks]
        return [c.__name__ for c in callbacks]

    def test_a_fallback_model_adds_the_callback_after_the_error_recorder(self):
        # The recorder returns None, so it must run first or it would never see the error
        with patch.dict(os.environ, {"MATE_PLUGINS_ENABLED": "false"}):
            agent = self._build(model_name="openai/primary", fallback_model="anthropic/fallback")
        self.assertEqual(self._callback_names(agent),
                         ["record_model_error_callback", "model_fallback_callback"])

    def test_no_fallback_model_adds_nothing(self):
        agent = self._build(model_name="openai/primary")
        self.assertNotIn("model_fallback_callback", self._callback_names(agent))

    def test_a_fallback_equal_to_the_model_is_ignored(self):
        agent = self._build(model_name="openai/primary", fallback_model=" openai/primary ")
        self.assertNotIn("model_fallback_callback", self._callback_names(agent))

    def test_the_primary_keeps_its_endpoint(self):
        agent = self._build(model_name="openai/primary", fallback_model="anthropic/fallback",
                            model_base_url="http://127.0.0.1:9000/v1", model_api_key="sk-literal")
        self.assertEqual(agent.model._additional_args.get("base_url"), "http://127.0.0.1:9000/v1")


class TestLangGraph(unittest.TestCase):

    def test_litellm_model_name_matches_what_create_chat_model_sends(self):
        # The run tells the fallback's answers apart by this name
        from shared.utils.langgraph.model_factory import create_chat_model, litellm_model_name
        for name in ("gemini-2.5-flash", "models/gemini-2.5-pro", "openai/gpt-4o",
                     "lm_studio/qwen", "anthropic/claude-sonnet-4-20250514",
                     "openrouter/deepseek/deepseek-chat"):
            self.assertEqual(litellm_model_name(name), create_chat_model(name).model, name)

    def test_the_event_carries_the_model_that_answered(self):
        from langchain_core.messages import AIMessage
        from shared.utils.langgraph.event_translator import ai_message_to_event
        message = AIMessage(content="hi", response_metadata={"model_name": "anthropic/fallback"})
        event = ai_message_to_event(message, "support", "inv1")
        self.assertEqual(event["modelVersion"], "anthropic/fallback")

    def test_react_agent_answers_with_the_fallback(self):
        from langgraph.prebuilt import create_react_agent
        from langchain_core.tools import tool
        from langchain_litellm import ChatLiteLLM
        from shared.utils.langgraph.event_translator import translate_stream

        @tool
        def lookup(order: str) -> str:
            """Look up an order."""
            return "shipped"

        primary = ChatLiteLLM(model="openai/primary", streaming=True, api_key="x",
                              model_kwargs={"mock_response": _provider_down()})
        fallback = ChatLiteLLM(model="anthropic/fallback", streaming=True, api_key="x",
                               model_kwargs={"mock_response": "hello from the fallback"})
        graph = create_react_agent(primary.with_fallbacks([fallback]), tools=[lookup])

        async def go():
            stream = graph.astream({"messages": [("user", "hi")]},
                                   stream_mode=["messages", "updates"], subgraphs=True)
            return [event async for event, complete in translate_stream(stream, "support", "inv1")
                    if complete]
        events = asyncio.run(go())
        self.assertEqual(events[-1]["content"]["parts"], [{"text": "hello from the fallback"}])
        self.assertEqual(events[-1]["modelVersion"], "anthropic/fallback")

    def test_the_built_agent_answers_with_the_fallback(self):
        from langchain_litellm import ChatLiteLLM
        from shared.utils.langgraph.agent_builder import AgentBuilder
        from shared.utils.langgraph.event_translator import translate_stream

        created = []

        def chat_model(name, generate_content_config=None, api_key=None, base_url=None):
            created.append((name, api_key, base_url))
            reply = _provider_down() if name == "openai/primary" else "hello from the fallback"
            return ChatLiteLLM(model=name, streaming=True, api_key="x",
                               model_kwargs={"mock_response": reply})

        config = {"name": "support", "type": "llm", "model_name": "openai/primary",
                  "fallback_model": "anthropic/fallback", "instruction": "Be brief.",
                  "model_base_url": "http://127.0.0.1:9000/v1", "model_api_key": "sk-literal"}
        builder = AgentBuilder()

        async def go():
            with patch("shared.utils.langgraph.agent_builder._load_child_configs", return_value=[]), \
                    patch.object(AgentBuilder, "_build_tools", return_value=[]), \
                    patch("shared.utils.langgraph.model_factory.create_chat_model",
                          side_effect=chat_model):
                built = await builder.build_for_config(config)
            stream = built.graph.astream({"messages": [("user", "hi")]},
                                         stream_mode=["messages", "updates"], subgraphs=True)
            events = [event async for event, complete in translate_stream(stream, "support", "inv1")
                      if complete]
            return built, events

        built, events = asyncio.run(go())
        self.assertEqual(built.fallback_models, {"support": "anthropic/fallback"})
        self.assertEqual(events[-1]["content"]["parts"], [{"text": "hello from the fallback"}])
        # The endpoint and key go to the agent's own model only
        self.assertEqual(created, [("openai/primary", "sk-literal", "http://127.0.0.1:9000/v1"),
                                   ("anthropic/fallback", None, None)])

    def test_the_built_agent_does_not_fall_back_on_a_request_error(self):
        from langchain_litellm import ChatLiteLLM
        from shared.utils.langgraph.agent_builder import AgentBuilder
        refused = litellm.ContentPolicyViolationError("refused", model="primary", llm_provider="openai")

        def chat_model(name, generate_content_config=None, api_key=None, base_url=None):
            reply = refused if name == "openai/primary" else "hello from the fallback"
            return ChatLiteLLM(model=name, streaming=True, api_key="x",
                               model_kwargs={"mock_response": reply})

        config = {"name": "support", "type": "llm", "model_name": "openai/primary",
                  "fallback_model": "anthropic/fallback", "instruction": "Be brief."}

        async def go():
            with patch("shared.utils.langgraph.agent_builder._load_child_configs", return_value=[]), \
                    patch.object(AgentBuilder, "_build_tools", return_value=[]), \
                    patch("shared.utils.langgraph.model_factory.create_chat_model",
                          side_effect=chat_model):
                built = await AgentBuilder().build_for_config(config)
            return await built.graph.ainvoke({"messages": [("user", "hi")]})

        with self.assertRaises(litellm.ContentPolicyViolationError):
            asyncio.run(go())

    def _log(self, model_version, fallback_models):
        from shared.utils.langgraph.executor import _log_token_usage
        event = {"id": "e1", "invocationId": "inv1", "author": "support",
                 "modelVersion": model_version,
                 "usageMetadata": {"prompt_token_count": 3, "candidates_token_count": 2}}
        service = MagicMock()
        with patch("shared.utils.token_usage_service.get_token_usage_service", return_value=service), \
                patch("shared.utils.audit_service.log") as audit:
            _log_token_usage(event, "support", "u1", "s1", {"support": "openai/primary"},
                             fallback_models)
        return service.log_token_usage.call_args.kwargs["model_name"], audit

    def test_a_fallback_answer_is_booked_to_the_fallback_and_audited(self):
        model, audit = self._log("anthropic/fallback", {"support": "anthropic/fallback"})
        self.assertEqual(model, "anthropic/fallback")
        audit.assert_called_once()

    def test_a_primary_answer_is_booked_as_before(self):
        model, audit = self._log("openai/primary", {"support": "anthropic/fallback"})
        self.assertEqual(model, "openai/primary")
        audit.assert_not_called()

    def test_without_a_fallback_nothing_changes(self):
        model, audit = self._log("anthropic/fallback", None)
        self.assertEqual(model, "openai/primary")
        audit.assert_not_called()

    def test_a_fallback_the_same_as_the_model_is_ignored(self):
        from shared.utils.langgraph.agent_builder import _fallback_model
        self.assertIsNone(_fallback_model({"model_name": "gemini-2.5-flash",
                                           "fallback_model": "models/gemini-2.5-flash"}))
        self.assertEqual(_fallback_model({"model_name": "gemini-2.5-flash",
                                          "fallback_model": " openai/gpt-4o "}), "openai/gpt-4o")


if __name__ == "__main__":
    unittest.main()
