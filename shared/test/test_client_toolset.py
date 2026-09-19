#!/usr/bin/env python3
"""
The ADK side of caller-declared tools.

A client tool must reach the model as a declaration built from the caller's own
JSON Schema, and must be long-running so the runtime emits the call and stops
rather than trying to execute a function that lives on the caller's machine.
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.tools.client_toolset import (CLIENT_TOOL_METADATA_KEY,
                                               ClientTool, ClientToolset,
                                               json_schema_to_schema)


class _RunConfig:
    def __init__(self, metadata):
        self.custom_metadata = metadata


class _Context:
    def __init__(self, metadata):
        self.run_config = _RunConfig(metadata)


def _context(declarations):
    return _Context({CLIENT_TOOL_METADATA_KEY: declarations})


READ = {
    "name": "read",
    "description": "Read a file",
    "parameters": {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "the path"}},
        "required": ["path"],
    },
}


class TestToolsetResolution(unittest.TestCase):

    def test_no_metadata_contributes_nothing(self):
        # Every caller that is not the OpenAI bridge must be unaffected.
        self.assertEqual(asyncio.run(ClientToolset().get_tools(None)), [])
        self.assertEqual(asyncio.run(ClientToolset().get_tools(_Context(None))), [])
        self.assertEqual(asyncio.run(ClientToolset().get_tools(_Context({}))), [])

    def test_declarations_become_long_running_tools(self):
        tools = asyncio.run(ClientToolset().get_tools(_context([READ])))
        self.assertEqual([t.name for t in tools], ["read"])
        # Long-running is what makes ADK emit the call and end the invocation
        # instead of synthesising an empty response.
        self.assertTrue(tools[0].is_long_running)

    def test_the_declaration_comes_from_the_callers_schema(self):
        tools = asyncio.run(ClientToolset().get_tools(_context([READ])))
        declaration = tools[0]._get_declaration()
        self.assertEqual(declaration.name, "read")
        self.assertIn("path", declaration.parameters.properties)
        self.assertEqual(declaration.parameters.required, ["path"])

    def test_the_agents_own_tool_wins_a_name_collision(self):
        tools = asyncio.run(
            ClientToolset(reserved_names={"read"}).get_tools(_context([READ])))
        self.assertEqual(tools, [])

    def test_malformed_declarations_are_skipped_not_fatal(self):
        tools = asyncio.run(ClientToolset().get_tools(
            _context([{"description": "nameless"}, "not a dict", READ])))
        self.assertEqual([t.name for t in tools], ["read"])

    def test_the_body_returns_nothing(self):
        # Returning None is the signal ADK reads as "still outstanding".
        tool = ClientTool("read", "Read a file", READ["parameters"])
        self.assertIsNone(tool.func())


class TestSchemaConversion(unittest.TestCase):

    def test_a_plain_object(self):
        schema = json_schema_to_schema(
            {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]})
        self.assertEqual(schema.type.value.upper(), "OBJECT")
        self.assertEqual(schema.required, ["a"])

    def test_an_optional_field_spelled_as_a_type_list(self):
        schema = json_schema_to_schema({"type": ["string", "null"]})
        self.assertEqual(schema.type.value.upper(), "STRING")
        self.assertTrue(schema.nullable)

    def test_a_string_enum_survives(self):
        schema = json_schema_to_schema({"type": "string", "enum": ["a", "b"]})
        self.assertEqual(schema.enum, ["a", "b"])

    def test_a_non_string_enum_is_dropped_rather_than_rejected(self):
        # Gemini only accepts string enums; keeping the bare type beats losing
        # the whole tool declaration.
        schema = json_schema_to_schema({"type": "integer", "enum": [1, 2]})
        self.assertEqual(schema.type.value.upper(), "INTEGER")
        self.assertFalse(schema.enum)

    def test_an_array_carries_its_item_type(self):
        schema = json_schema_to_schema({"type": "array", "items": {"type": "string"}})
        self.assertEqual(schema.type.value.upper(), "ARRAY")
        self.assertEqual(schema.items.type.value.upper(), "STRING")

    def test_a_nested_object(self):
        schema = json_schema_to_schema({
            "type": "object",
            "properties": {"opts": {"type": "object",
                                    "properties": {"deep": {"type": "boolean"}}}},
        })
        self.assertIn("deep", schema.properties["opts"].properties)

    def test_any_of_is_preserved(self):
        schema = json_schema_to_schema(
            {"anyOf": [{"type": "string"}, {"type": "integer"}]})
        self.assertEqual(len(schema.any_of), 2)

    def test_junk_falls_back_to_an_untyped_object(self):
        self.assertEqual(json_schema_to_schema(None).type.value.upper(), "OBJECT")
        self.assertEqual(json_schema_to_schema("nonsense").type.value.upper(), "OBJECT")


if __name__ == "__main__":
    unittest.main()
