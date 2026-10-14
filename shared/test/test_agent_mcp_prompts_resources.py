#!/usr/bin/env python3
"""
An agent's MCP server offers one prompt (ask the agent through its tool) and one
resource (what the agent does and how to call it), next to its one tool. MCP
registries such as the LobeHub market list servers by these capabilities.
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from shared.utils.tools.agent_mcp_protocol_handler import AgentMCPProtocolHandler


class _Request:
    def __init__(self, body: dict):
        self.body = body

    async def json(self) -> dict:
        return self.body


def call(handler: AgentMCPProtocolHandler, method: str, **params) -> dict:
    body = {"jsonrpc": "2.0", "id": 7, "method": method, "params": params}
    return asyncio.run(handler.handle_mcp_request(_Request(body)))


class TestAgentMcpPromptsAndResources(unittest.TestCase):

    def setUp(self):
        self.handler = AgentMCPProtocolHandler("helper", "Helps with things.")

    def test_initialize_announces_prompts_and_resources(self):
        caps = call(self.handler, "initialize")["result"]["capabilities"]
        self.assertEqual(set(caps), {"tools", "prompts", "resources"})

    def test_prompt_asks_the_agent_through_its_tool(self):
        prompts = call(self.handler, "prompts/list")["result"]["prompts"]
        self.assertEqual([p["name"] for p in prompts], ["ask_helper"])
        self.assertTrue(prompts[0]["arguments"][0]["required"])

        result = call(self.handler, "prompts/get", name="ask_helper",
                      arguments={"question": "What can you do?"})["result"]
        text = result["messages"][0]["content"]["text"]
        self.assertIn("call_helper_agent", text)
        self.assertTrue(text.endswith("What can you do?"))

    def test_prompt_errors(self):
        self.assertEqual(call(self.handler, "prompts/get", name="other",
                              arguments={"question": "x"})["error"]["code"], -32602)
        self.assertEqual(call(self.handler, "prompts/get", name="ask_helper",
                              arguments={})["error"]["code"], -32602)

    def test_resource_describes_the_agent_and_its_tool(self):
        resources = call(self.handler, "resources/list")["result"]["resources"]
        self.assertEqual([r["uri"] for r in resources], ["mate://agents/helper/about"])

        contents = call(self.handler, "resources/read",
                        uri="mate://agents/helper/about")["result"]["contents"]
        self.assertIn("Helps with things.", contents[0]["text"])
        self.assertIn("call_helper_agent", contents[0]["text"])

    def test_unknown_resource(self):
        self.assertEqual(call(self.handler, "resources/read",
                              uri="mate://agents/other/about")["error"]["code"], -32002)


if __name__ == "__main__":
    unittest.main()
