#!/usr/bin/env python3
"""
Unit tests for the mystery game tools (session-scoped detective cases)
and the mystery-generator agent template.
"""

import copy
import json
import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from pathlib import Path
from types import SimpleNamespace

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.tools.mystery_game import (
    DEFAULT_CASE,
    STATE_KEY,
    SUSPECT_COUNT,
    create_mystery_character_tools_from_config,
    create_mystery_gm_tools_from_config,
    validate_case,
)


def _fake_context(agent_name="mystery_gm_root", state=None):
    return SimpleNamespace(state=state if state is not None else {}, agent_name=agent_name)


def _gm_tools():
    tools = create_mystery_gm_tools_from_config({})
    return {t.__name__: t for t in tools}


class TestValidateCase(unittest.TestCase):

    def test_default_case_is_valid(self):
        self.assertIsNone(validate_case(copy.deepcopy(DEFAULT_CASE)))

    def test_rejects_non_dict(self):
        self.assertIsNotNone(validate_case(None))
        self.assertIsNotNone(validate_case([]))

    def test_rejects_wrong_suspect_count(self):
        case = copy.deepcopy(DEFAULT_CASE)
        case["suspects"] = case["suspects"][:2]
        self.assertIn("suspects", validate_case(case))

    def test_rejects_two_killers(self):
        case = copy.deepcopy(DEFAULT_CASE)
        case["suspects"][0]["is_killer"] = True
        case["suspects"][0]["killer_brief"] = "also guilty"
        self.assertIn("exactly one", validate_case(case))

    def test_rejects_missing_solution_field(self):
        case = copy.deepcopy(DEFAULT_CASE)
        del case["solution"]["motive"]
        self.assertIn("solution", validate_case(case))

    def test_coerces_list_valued_text_fields(self):
        # Models sometimes emit bullet fields as arrays of strings
        case = copy.deepcopy(DEFAULT_CASE)
        case["suspects"][0]["knowledge"] = ["prva stavka", "druga stavka"]
        case["solution"]["evidence_chain"] = ["nalaz", "pismo"]
        self.assertIsNone(validate_case(case))
        self.assertEqual(case["suspects"][0]["knowledge"], "prva stavka\ndruga stavka")
        self.assertEqual(case["solution"]["evidence_chain"], "nalaz\npismo")

    def test_rejects_killer_name_mismatch(self):
        case = copy.deepcopy(DEFAULT_CASE)
        case["solution"]["killer"] = "Neko Sasvim Drugi"
        self.assertIn("does not match", validate_case(case))


class TestGmTools(unittest.TestCase):

    def test_get_case_brief_loads_default_and_hides_solution(self):
        ctx = _fake_context()
        result = _gm_tools()["get_case_brief"](tool_context=ctx)
        self.assertEqual(result["status"], "success")
        # Default case stored in session state
        self.assertIn(STATE_KEY, ctx.state)
        brief = result["case"]
        self.assertEqual(len(brief["suspects"]), SUSPECT_COUNT)
        # Suspects map to the generic actor agents
        agents = {s["interrogation_agent"] for s in brief["suspects"]}
        self.assertEqual(agents, {f"mystery_suspect_{i}" for i in range(1, SUSPECT_COUNT + 1)})
        # No spoilers anywhere in the brief
        dumped = json.dumps(brief, ensure_ascii=False).lower()
        self.assertNotIn("solution", dumped)
        self.assertNotIn("is_killer", dumped)
        self.assertNotIn("digitalis", dumped)

    def test_get_case_evidence_by_id_and_title(self):
        ctx = _fake_context()
        tools = _gm_tools()
        by_id = tools["get_case_evidence"](evidence_id="forensics", tool_context=ctx)
        self.assertEqual(by_id["status"], "success")
        self.assertIn("DIGITALIS", by_id["evidence"]["content"])
        by_title = tools["get_case_evidence"](evidence_id="biblioteka", tool_context=ctx)
        self.assertEqual(by_title["status"], "success")
        self.assertEqual(by_title["evidence"]["id"], "scene")

    def test_get_case_evidence_unknown_id(self):
        result = _gm_tools()["get_case_evidence"](evidence_id="nema", tool_context=_fake_context())
        self.assertEqual(result["status"], "error")
        self.assertIn("scene", result["error_message"])

    def test_check_accusation_correct(self):
        result = _gm_tools()["check_accusation"](accused_name="dr Ana Simić", tool_context=_fake_context())
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["correct"])
        self.assertIn("digitalis", result["solution"]["method"].casefold())

    def test_check_accusation_wrong_does_not_leak_killer(self):
        result = _gm_tools()["check_accusation"](accused_name="Žarko", tool_context=_fake_context())
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["correct"])
        self.assertTrue(result["why_innocent"])
        self.assertNotIn("Ana", json.dumps(result, ensure_ascii=False))

    def test_check_accusation_unknown_name(self):
        result = _gm_tools()["check_accusation"](accused_name="Petar Petrović", tool_context=_fake_context())
        self.assertEqual(result["status"], "error")


class TestCharacterTool(unittest.TestCase):

    def _get_my_character(self, agent_name, state=None):
        tool = create_mystery_character_tools_from_config({})[0]
        return tool(tool_context=_fake_context(agent_name=agent_name, state=state))

    def test_innocent_suspect_sheet(self):
        result = self._get_my_character("mystery_suspect_1")
        self.assertEqual(result["status"], "success")
        sheet = result["character"]
        self.assertEqual(sheet["name"], "Žarko Obradović")
        self.assertIn("NOT the killer", sheet["confidential_role"])

    def test_killer_suspect_sheet(self):
        result = self._get_my_character("mystery_suspect_4")
        self.assertEqual(result["status"], "success")
        sheet = result["character"]
        self.assertEqual(sheet["name"], "dr Ana Simić")
        self.assertIn("POČINILAC", sheet["confidential_role"])

    def test_reads_case_from_state_not_default(self):
        case = copy.deepcopy(DEFAULT_CASE)
        case["suspects"][0]["name"] = "Novi Lik"
        result = self._get_my_character("mystery_suspect_1", state={STATE_KEY: case})
        self.assertEqual(result["character"]["name"], "Novi Lik")

    def test_unresolvable_agent_name(self):
        result = self._get_my_character("mystery_gm_root")
        self.assertEqual(result["status"], "error")


class TestGenerateNewCase(unittest.TestCase):

    def _llm_response(self, content):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])

    def test_valid_generation_replaces_state(self):
        new_case = copy.deepcopy(DEFAULT_CASE)
        new_case["title"] = "Smrt na splavu"
        ctx = _fake_context()
        with patch("litellm.completion", return_value=self._llm_response(json.dumps(new_case, ensure_ascii=False))) as mock_llm:
            result = _gm_tools()["generate_new_case"](language="Serbian", theme="reka", tool_context=ctx)
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["case"]["title"], "Smrt na splavu")
        self.assertEqual(ctx.state[STATE_KEY]["title"], "Smrt na splavu")
        prompt = mock_llm.call_args.kwargs["messages"][0]["content"]
        self.assertIn("Serbian", prompt)
        self.assertIn("reka", prompt)

    def test_invalid_generation_keeps_current_case_and_errors(self):
        ctx = _fake_context(state={STATE_KEY: copy.deepcopy(DEFAULT_CASE)})
        with patch("litellm.completion", return_value=self._llm_response("not json at all")) as mock_llm:
            result = _gm_tools()["generate_new_case"](language="Serbian", tool_context=ctx)
        self.assertEqual(result["status"], "error")
        self.assertEqual(mock_llm.call_count, 2)  # one retry
        self.assertEqual(ctx.state[STATE_KEY]["title"], DEFAULT_CASE["title"])

    def test_model_from_tool_config_is_used(self):
        tools = create_mystery_gm_tools_from_config(
            {"tool_config": json.dumps({"mystery_gm": {"model": "openrouter/test/custom-model"}})})
        generate = next(t for t in tools if t.__name__ == "generate_new_case")
        ctx = _fake_context()
        with patch("litellm.completion",
                   return_value=self._llm_response(json.dumps(copy.deepcopy(DEFAULT_CASE), ensure_ascii=False))) as mock_llm:
            result = generate(language="Serbian", tool_context=ctx)
        self.assertEqual(result["status"], "success")
        self.assertEqual(mock_llm.call_args.kwargs["model"], "openrouter/test/custom-model")

    def test_boolean_tool_config_falls_back_to_default_model(self):
        from shared.utils.tools.mystery_game import _default_model
        tools = create_mystery_gm_tools_from_config({"tool_config": json.dumps({"mystery_gm": True})})
        generate = next(t for t in tools if t.__name__ == "generate_new_case")
        with patch("litellm.completion",
                   return_value=self._llm_response(json.dumps(copy.deepcopy(DEFAULT_CASE), ensure_ascii=False))) as mock_llm:
            result = generate(language="Serbian", tool_context=_fake_context())
        self.assertEqual(result["status"], "success")
        self.assertEqual(mock_llm.call_args.kwargs["model"], _default_model())

    def test_setting_and_method_seeds_vary(self):
        # Without seeding, the model kept writing the same theatre whodunit.
        seen = set()
        for _ in range(20):
            with patch("litellm.completion",
                       return_value=self._llm_response(json.dumps(copy.deepcopy(DEFAULT_CASE), ensure_ascii=False))) as mock_llm:
                _gm_tools()["generate_new_case"](language="Serbian", tool_context=_fake_context())
            prompt = mock_llm.call_args.kwargs["messages"][0]["content"]
            self.assertIn("Set the case here:", prompt)
            self.assertIn("The murder method must be:", prompt)
            seen.add(prompt.split("Set the case here:")[1].split("\n")[0])
        self.assertGreater(len(seen), 1, "setting never varied")

    def test_theme_replaces_random_setting(self):
        with patch("litellm.completion",
                   return_value=self._llm_response(json.dumps(copy.deepcopy(DEFAULT_CASE), ensure_ascii=False))) as mock_llm:
            _gm_tools()["generate_new_case"](language="Serbian", theme="misterija na moru",
                                             tool_context=_fake_context())
        prompt = mock_llm.call_args.kwargs["messages"][0]["content"]
        self.assertIn("misterija na moru", prompt)
        self.assertNotIn("Set the case here:", prompt)
        # The method seed still applies on top of the player's theme
        self.assertIn("The murder method must be:", prompt)

    def test_killer_position_is_randomized(self):
        # Models write the culprit last; generation must shuffle so the last card
        # is not always the killer.
        killer_positions = set()
        for _ in range(25):
            case = copy.deepcopy(DEFAULT_CASE)  # killer is suspect 4 (last)
            ctx = _fake_context()
            with patch("litellm.completion",
                       return_value=self._llm_response(json.dumps(case, ensure_ascii=False))):
                result = _gm_tools()["generate_new_case"](language="Serbian", tool_context=ctx)
            self.assertEqual(result["status"], "success")
            stored = ctx.state[STATE_KEY]["suspects"]
            killer_positions.add(next(i for i, s in enumerate(stored) if s.get("is_killer")))
            # ids must be renumbered to match the new order, so suspect agent
            # mystery_suspect_N keeps returning the sheet at position N
            self.assertEqual([s["id"] for s in stored], list(range(1, SUSPECT_COUNT + 1)))
        self.assertGreater(len(killer_positions), 1, "killer position never varied")

    def test_shuffled_case_stays_consistent(self):
        case = copy.deepcopy(DEFAULT_CASE)
        ctx = _fake_context()
        with patch("litellm.completion",
                   return_value=self._llm_response(json.dumps(case, ensure_ascii=False))):
            _gm_tools()["generate_new_case"](language="Serbian", tool_context=ctx)
        # Accusation check and character sheets still resolve after the shuffle
        self.assertTrue(_gm_tools()["check_accusation"](
            accused_name="dr Ana Simić", tool_context=ctx)["correct"])
        killer_slot = next(s["id"] for s in ctx.state[STATE_KEY]["suspects"] if s["is_killer"])
        sheet = create_mystery_character_tools_from_config({})[0](
            tool_context=_fake_context(agent_name=f"mystery_suspect_{killer_slot}",
                                       state=ctx.state))
        self.assertEqual(sheet["character"]["name"], "dr Ana Simić")
        self.assertIn("POČINILAC", sheet["character"]["confidential_role"])

    def test_markdown_fenced_json_is_accepted(self):
        fenced = "```json\n" + json.dumps(copy.deepcopy(DEFAULT_CASE), ensure_ascii=False) + "\n```"
        ctx = _fake_context()
        with patch("litellm.completion", return_value=self._llm_response(fenced)):
            result = _gm_tools()["generate_new_case"](language="English", tool_context=ctx)
        self.assertEqual(result["status"], "success")


class TestMysteryGeneratorTemplate(unittest.TestCase):

    def test_load_mystery_generator_template(self):
        from shared.utils.template_service import TemplateService
        project_root = Path(__file__).parent.parent.parent
        template = TemplateService(project_root=project_root).get_template("mystery-generator")
        self.assertIsNotNone(template)

        meta = template.get("template_meta", {})
        self.assertEqual(meta.get("id"), "mystery-generator")
        self.assertEqual(meta.get("category"), "demo")
        self.assertEqual(meta.get("root_agent"), "mystery_gm_root")

        # Root + 4 generic suspect actors
        agents = template.get("agents", [])
        self.assertEqual(len(agents), 5)

        root_agent = next((a for a in agents if a["name"] == "mystery_gm_root"), None)
        self.assertIsNotNone(root_agent)
        self.assertEqual(root_agent.get("parent_agents"), [])
        gm_cfg = json.loads(root_agent["tool_config"]).get("mystery_gm")
        self.assertTrue(gm_cfg)
        # Generation model configured per-agent via tool_config (no env/restart needed)
        self.assertTrue(gm_cfg.get("model", "").startswith("openrouter/"))

        suspects = [a for a in agents if a["name"] != "mystery_gm_root"]
        self.assertEqual({a["name"] for a in suspects},
                         {f"mystery_suspect_{i}" for i in range(1, 5)})
        for suspect in suspects:
            self.assertEqual(suspect.get("parent_agents"), ["mystery_gm_root"])
            self.assertTrue(json.loads(suspect["tool_config"]).get("mystery_character"))
            self.assertEqual(suspect.get("model_name"), "openrouter/google/gemini-3-flash-preview")
            guardrails = json.loads(suspect["guardrail_config"])["guardrails"]
            types = {g["type"] for g in guardrails if g.get("enabled")}
            self.assertIn("prompt_injection", types)
            self.assertIn("content_policy", types)

        # Session-scoped design: no shared memory blocks that could leak between users
        self.assertEqual(template.get("memory_blocks", []), [])

    def test_tool_factory_knows_mystery_tool_types(self):
        from shared.utils.tools.tool_factory import ToolFactory
        factory = ToolFactory()
        self.assertIn("mystery_gm", factory._tool_creators)
        self.assertIn("mystery_character", factory._tool_creators)

        gm_tools = factory._tool_creators["mystery_gm"]({})
        self.assertEqual({t.__name__ for t in gm_tools},
                         {"get_case_brief", "get_case_evidence", "check_accusation", "generate_new_case"})
        char_tools = factory._tool_creators["mystery_character"]({})
        self.assertEqual([t.__name__ for t in char_tools], ["get_my_character"])


if __name__ == '__main__':
    unittest.main()
