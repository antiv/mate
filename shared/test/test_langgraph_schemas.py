#!/usr/bin/env python3
"""
Tests for output_schema, input_schema and include_contents on the LangGraph
runtime, with a scripted chat model (no provider key needed).
"""

import asyncio
import json
import os
import sys
import unittest
from typing import Any, List
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from shared.utils.langgraph.agent_builder import (
    SET_MODEL_RESPONSE_TOOL_NAME,
    AgentBuilder,
    _current_turn,
    _make_prompt,
    _schema_model,
)
from shared.utils.langgraph.event_translator import translate_stream

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {"city": {"type": "string"}, "temp": {"type": "integer"}},
    "required": ["city", "temp"],
}


def _run(coro):
    return asyncio.run(coro)


class ScriptedModel(BaseChatModel):
    """Replies with the scripted messages in order and records what it was shown."""

    script: List[Any]
    seen: List[Any] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.seen.append(list(messages))
        reply = self.script[len(self.seen) - 1]
        return ChatResult(generations=[ChatGeneration(message=reply)])


def _call(name, args, call_id="c1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def _config(**extra):
    config = {
        "name": "weather_agent", "type": "llm", "model_name": "openai/gpt-test",
        "description": "", "instruction": "You report weather.",
        "tool_config": None, "mcp_servers_config": None,
        "generate_content_config": None, "planner_config": None,
        "guardrail_config": None, "parent_agents": [],
    }
    config.update(extra)
    return config


@tool
def lookup(city: str) -> str:
    """Look up the temperature of a city."""
    return f"{city}: 21"


def _build(config, model, tools=None):
    builder = AgentBuilder()

    async def build_tools(_config):
        return list(tools or [])

    with patch("shared.utils.langgraph.agent_builder.AgentBuilder._build_tools", side_effect=build_tools), \
            patch("shared.utils.langgraph.model_factory.create_chat_model", return_value=model), \
            patch("shared.utils.utils.resolve_agent_endpoint", return_value=(None, None)), \
            patch("shared.utils.langgraph.hooks.get_user_profile_block", return_value=None):
        return _run(builder._build_react_agent(config, {}))


async def _events(graph, messages):
    stream = graph.astream({"messages": messages}, stream_mode=["messages", "updates"],
                           subgraphs=True)
    return [event async for event, complete in translate_stream(stream, "weather_agent", "inv")
            if complete]


def _texts(events):
    return [part["text"] for event in events
            for part in event.get("content", {}).get("parts", []) if "text" in part]


class TestSchemaModel(unittest.TestCase):

    def test_json_string_and_dict_give_the_same_model(self):
        from_dict = _schema_model({"name": "a_b", "output_schema": OUTPUT_SCHEMA}, "output_schema", "OutputModel")
        from_str = _schema_model({"name": "a_b", "output_schema": json.dumps(OUTPUT_SCHEMA)},
                                 "output_schema", "OutputModel")
        self.assertEqual(from_dict.model_json_schema()["required"], ["city", "temp"])
        self.assertEqual(from_str.__name__, "AbOutputModel")

    def test_missing_or_invalid_schema_is_ignored(self):
        for raw in (None, "", "{not json", {"type": "string"}):
            self.assertIsNone(_schema_model({"name": "a", "output_schema": raw}, "output_schema", "OutputModel"))


class TestOutputSchema(unittest.TestCase):

    def test_final_answer_is_json_matching_the_schema(self):
        model = ScriptedModel(script=[_call(SET_MODEL_RESPONSE_TOOL_NAME, {"city": "Oslo", "temp": 4})])
        graph = _build(_config(output_schema=json.dumps(OUTPUT_SCHEMA)), model)
        events = _run(_events(graph, [HumanMessage(content="weather in Oslo?")]))
        texts = _texts(events)
        self.assertEqual(json.loads(texts[-1]), {"city": "Oslo", "temp": 4})
        # the call itself is not shown, only its validated JSON text
        calls = [p for e in events for p in e.get("content", {}).get("parts", []) if "functionCall" in p]
        self.assertEqual(calls, [])
        # the model was told to answer through the tool
        system = model.seen[0][0].content
        self.assertIn(SET_MODEL_RESPONSE_TOOL_NAME, system)

    def test_tools_still_work_before_the_final_answer(self):
        model = ScriptedModel(script=[
            _call("lookup", {"city": "Oslo"}),
            _call(SET_MODEL_RESPONSE_TOOL_NAME, {"city": "Oslo", "temp": 21}, "c2"),
        ])
        graph = _build(_config(output_schema=OUTPUT_SCHEMA), model, tools=[lookup])
        events = _run(_events(graph, [HumanMessage(content="weather in Oslo?")]))
        names = [p["functionCall"]["name"] for e in events
                 for p in e.get("content", {}).get("parts", []) if "functionCall" in p]
        self.assertEqual(names, ["lookup"])
        self.assertEqual(json.loads(_texts(events)[-1]), {"city": "Oslo", "temp": 21})

    def test_answer_violating_the_schema_is_not_returned_as_valid_json(self):
        model = ScriptedModel(script=[_call(SET_MODEL_RESPONSE_TOOL_NAME, {"city": "Oslo", "temp": "warm"})])
        graph = _build(_config(output_schema=OUTPUT_SCHEMA), model)
        events = _run(_events(graph, [HumanMessage(content="weather in Oslo?")]))
        # the failed validation is reported as a tool result, not hidden
        responses = [p["functionResponse"] for e in events
                     for p in e.get("content", {}).get("parts", []) if "functionResponse" in p]
        self.assertEqual(len(responses), 1)
        self.assertEqual(responses[0]["name"], SET_MODEL_RESPONSE_TOOL_NAME)
        self.assertIn("temp", responses[0]["response"]["result"])
        # no text event carries the invalid JSON, and the call is shown with its error
        self.assertEqual(_texts(events), [])
        calls = [p["functionCall"] for e in events
                 for p in e.get("content", {}).get("parts", []) if "functionCall" in p]
        self.assertEqual([c["name"] for c in calls], [SET_MODEL_RESPONSE_TOOL_NAME])
        self.assertEqual(calls[0]["args"], {"city": "Oslo", "temp": "warm"})

    def test_reply_is_the_validated_json_not_the_raw_arguments(self):
        model = ScriptedModel(script=[_call(SET_MODEL_RESPONSE_TOOL_NAME, {"city": "Oslo", "temp": "4"})])
        graph = _build(_config(output_schema=OUTPUT_SCHEMA), model)
        events = _run(_events(graph, [HumanMessage(content="weather in Oslo?")]))
        self.assertEqual(_texts(events), ['{"city":"Oslo","temp":4}'])

    def test_without_output_schema_nothing_changes(self):
        model = ScriptedModel(script=[AIMessage(content="It is cold.")])
        graph = _build(_config(), model)
        events = _run(_events(graph, [HumanMessage(content="weather?")]))
        self.assertEqual(_texts(events), ["It is cold."])
        self.assertNotIn(SET_MODEL_RESPONSE_TOOL_NAME, model.seen[0][0].content)


class TestInputSchema(unittest.TestCase):

    def test_input_schema_is_accepted_and_has_no_run_time_effect(self):
        model = ScriptedModel(script=[AIMessage(content="ok")])
        graph = _build(_config(input_schema=json.dumps(OUTPUT_SCHEMA)), model)
        events = _run(_events(graph, [HumanMessage(content="not json at all")]))
        self.assertEqual(_texts(events), ["ok"])

    def test_invalid_input_schema_is_ignored(self):
        model = ScriptedModel(script=[AIMessage(content="ok")])
        graph = _build(_config(input_schema="{broken"), model)
        events = _run(_events(graph, [HumanMessage(content="hi")]))
        self.assertEqual(_texts(events), ["ok"])


class TestIncludeContents(unittest.TestCase):

    HISTORY = [
        HumanMessage(content="first question"),
        AIMessage(content="first answer"),
        HumanMessage(content="second question"),
    ]

    def _shown(self, include_contents):
        model = ScriptedModel(script=[AIMessage(content="done")])
        graph = _build(_config(include_contents=include_contents), model)
        _run(_events(graph, list(self.HISTORY)))
        return [m.content for m in model.seen[0] if m.type != "system"]

    def test_none_hides_earlier_turns(self):
        self.assertEqual(self._shown("none"), ["second question"])

    def test_default_and_empty_keep_the_history(self):
        everything = ["first question", "first answer", "second question"]
        self.assertEqual(self._shown("default"), everything)
        self.assertEqual(self._shown(""), everything)
        self.assertEqual(self._shown(None), everything)

    def test_none_keeps_this_turns_tool_calls_and_results(self):
        messages = self.HISTORY + [_call("lookup", {"city": "Oslo"}),
                                   ToolMessage(content="Oslo: 21", tool_call_id="c1", name="lookup")]
        self.assertEqual(_current_turn(messages), messages[2:])

    def test_no_human_message_keeps_everything(self):
        messages = [AIMessage(content="a")]
        self.assertEqual(_current_turn(messages), messages)

    def test_prompt_still_carries_the_instruction(self):
        prompt = _make_prompt("Be brief.", include_contents="none")
        with patch("shared.utils.langgraph.hooks.get_user_profile_block", return_value=None):
            built = prompt.invoke({"messages": list(self.HISTORY)})
        self.assertEqual(built[0].content, "Be brief.")
        self.assertEqual([m.content for m in built[1:]], ["second question"])


if __name__ == "__main__":
    unittest.main()
