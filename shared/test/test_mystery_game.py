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

    def test_rejects_suspect_without_false_lead(self):
        # Misleading testimony is what keeps the case from being a straight line
        case = copy.deepcopy(DEFAULT_CASE)
        case["suspects"][2]["false_lead"] = "  "
        self.assertIn("false_lead", validate_case(case))

    def test_rejects_solution_without_misdirection_fields(self):
        for field in ("red_herring", "turning_point"):
            case = copy.deepcopy(DEFAULT_CASE)
            del case["solution"][field]
            self.assertIn(field, validate_case(case))

    def test_rejects_innocent_without_rebuttal(self):
        case = copy.deepcopy(DEFAULT_CASE)
        case["suspects"][0]["rebuttal"] = ""
        self.assertIn("rebuttal", validate_case(case))

    def test_rejects_too_few_evidence_items(self):
        case = copy.deepcopy(DEFAULT_CASE)
        case["evidence"] = case["evidence"][:2]
        self.assertIn("evidence", validate_case(case))

    def test_default_case_spreads_the_suspicion(self):
        # Every suspect must carry a claim that can send the detective the wrong way
        for suspect in DEFAULT_CASE["suspects"]:
            self.assertTrue(suspect["false_lead"].strip(), suspect["name"])
        # ...and the means must not be exclusive to the culprit's profession
        forensics = next(e for e in DEFAULT_CASE["evidence"] if e["id"] == "forensics")
        self.assertIn("bočici koja nedostaje", forensics["content"])

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
        self.assertIn("digoksin", result["solution"]["method"].casefold())
        # The reveal explains the false trail the player was meant to fall for
        self.assertIn("Viktor", result["solution"]["red_herring"])
        self.assertTrue(result["solution"]["turning_point"])

    def test_check_accusation_wrong_does_not_leak_killer(self):
        result = _gm_tools()["check_accusation"](accused_name="Žarko", tool_context=_fake_context())
        self.assertEqual(result["status"], "success")
        self.assertFalse(result["correct"])
        self.assertTrue(result["why_innocent"])
        self.assertNotIn("Ana", json.dumps(result, ensure_ascii=False))

    def test_wrong_accusation_returns_only_the_bare_rebuttal(self):
        # Guessing must not out-teach investigating: the full reasoning is withheld
        ctx = _fake_context()
        result = _gm_tools()["check_accusation"](accused_name="Viktor Radan", tool_context=ctx)
        viktor = next(s for s in DEFAULT_CASE["suspects"] if s["name"] == "Viktor Radan")
        self.assertEqual(result["why_innocent"], viktor["rebuttal"])
        self.assertNotIn(viktor["not_killer_note"], json.dumps(result, ensure_ascii=False))

    def test_rebuttals_withhold_the_solution_facts(self):
        # A rebuttal is "partial" exactly when it shares no fact with the solving chain
        solution = DEFAULT_CASE["solution"]
        chain = f"{solution['turning_point']} {solution['evidence_chain']}".casefold()
        for suspect in DEFAULT_CASE["suspects"]:
            if suspect.get("is_killer"):
                continue
            rebuttal = suspect["rebuttal"].casefold()
            for giveaway in ("22:35", "22:45", "22:48", "22:54", "prsten", "toksikolog", "advokat"):
                self.assertNotIn(giveaway, rebuttal, f"{suspect['name']} rebuttal leaks '{giveaway}'")
            self.assertNotIn(rebuttal.strip("."), chain)

    def test_correct_accusation_clears_the_innocents(self):
        result = _gm_tools()["check_accusation"](accused_name="dr Ana Simić", tool_context=_fake_context())
        cleared = result["cleared_suspects"]
        self.assertEqual(len(cleared), SUSPECT_COUNT - 1)
        self.assertTrue(all(c["why_innocent"] for c in cleared))
        self.assertNotIn("Ana Simić", {c["name"] for c in cleared})

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

    def test_sheet_carries_the_false_lead(self):
        # The actor can only mislead the player if the wrong claim reaches its sheet
        sheet = self._get_my_character("mystery_suspect_2")["character"]
        self.assertIn("automobil", sheet["your_false_lead"])

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
        prompt = mock_llm.call_args_list[0].kwargs["messages"][0]["content"]
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
            prompt = mock_llm.call_args_list[0].kwargs["messages"][0]["content"]
            self.assertIn("Set the case here:", prompt)
            self.assertIn("The murder method must be:", prompt)
            seen.add(prompt.split("Set the case here:")[1].split("\n")[0])
        self.assertGreater(len(seen), 1, "setting never varied")

    def test_theme_replaces_random_setting(self):
        with patch("litellm.completion",
                   return_value=self._llm_response(json.dumps(copy.deepcopy(DEFAULT_CASE), ensure_ascii=False))) as mock_llm:
            _gm_tools()["generate_new_case"](language="Serbian", theme="misterija na moru",
                                             tool_context=_fake_context())
        prompt = mock_llm.call_args_list[0].kwargs["messages"][0]["content"]
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

    def test_prompt_demands_misdirection(self):
        with patch("litellm.completion",
                   return_value=self._llm_response(json.dumps(copy.deepcopy(DEFAULT_CASE), ensure_ascii=False))) as mock_llm:
            _gm_tools()["generate_new_case"](language="Serbian", tool_context=_fake_context())
        prompt = mock_llm.call_args_list[0].kwargs["messages"][0]["content"]
        self.assertIn("NO single piece of evidence may identify the killer on its own", prompt)
        self.assertIn("Pick ONE innocent as the apparent culprit", prompt)
        self.assertIn("false_lead", prompt)
        self.assertIn("red_herring", prompt)

    def test_retry_tells_the_model_what_was_wrong(self):
        # A blind retry of the same prompt tends to reproduce the same defect
        bad = copy.deepcopy(DEFAULT_CASE)
        bad["suspects"][0]["false_lead"] = ""
        with patch("litellm.completion",
                   return_value=self._llm_response(json.dumps(bad, ensure_ascii=False))) as mock_llm:
            result = _gm_tools()["generate_new_case"](language="Serbian", tool_context=_fake_context())
        self.assertEqual(result["status"], "error")
        self.assertEqual(mock_llm.call_count, 2)
        retry_prompt = mock_llm.call_args_list[1].kwargs["messages"][0]["content"]
        self.assertIn("previous attempt was rejected", retry_prompt)
        self.assertIn("false_lead", retry_prompt)

    def _case_then(self, *replies):
        """Generation reply followed by the probe/revision replies, in order."""
        case = json.dumps(copy.deepcopy(DEFAULT_CASE), ensure_ascii=False)
        return [self._llm_response(r) for r in (case,) + replies]

    def test_transparent_case_gets_its_surface_rewritten(self):
        probe = json.dumps({"culprit": "dr Ana Simić", "confidence": 5,
                            "reason": "the tox report says only a doctor can obtain it"})
        revision = json.dumps({
            "dossier": "Prepisani dosije bez navođenja.",
            "public_info": ["Kartica 1", "Kartica 2", "Kartica 3", "Kartica 4"],
            "evidence": [dict(e, content="Prepisan nalaz bez zaključka.\nDruga stavka.")
                         for e in DEFAULT_CASE["evidence"]],
        }, ensure_ascii=False)
        ctx = _fake_context()
        with patch("litellm.completion", side_effect=self._case_then(probe, revision)) as mock_llm:
            result = _gm_tools()["generate_new_case"](language="Serbian", tool_context=ctx)
        self.assertEqual(result["status"], "success")
        self.assertEqual(mock_llm.call_count, 3)  # generate, probe, revise
        stored = ctx.state[STATE_KEY]
        self.assertEqual(stored["dossier"], "Prepisani dosije bez navođenja.")
        self.assertIn("Prepisan nalaz", stored["evidence"][0]["content"])
        # The rewrite may only touch the surface — whodunit is untouchable
        self.assertEqual(stored["solution"], DEFAULT_CASE["solution"])
        killer = next(s for s in stored["suspects"] if s["is_killer"])
        self.assertEqual(killer["name"], "dr Ana Simić")
        self.assertTrue(killer["killer_brief"])

    def test_opaque_case_is_left_alone(self):
        probe = json.dumps({"culprit": "Viktor Radan", "confidence": 5, "reason": "the pen"})
        with patch("litellm.completion", side_effect=self._case_then(probe)) as mock_llm:
            result = _gm_tools()["generate_new_case"](language="Serbian", tool_context=_fake_context())
        self.assertEqual(result["status"], "success")
        self.assertEqual(mock_llm.call_count, 2)  # no revision needed

    def test_lucky_guess_does_not_trigger_a_rewrite(self):
        # Naming the killer among four with no idea why is chance, not transparency
        probe = json.dumps({"culprit": "dr Ana Simić", "confidence": 1, "reason": "a hunch"})
        with patch("litellm.completion", side_effect=self._case_then(probe)) as mock_llm:
            _gm_tools()["generate_new_case"](language="Serbian", tool_context=_fake_context())
        self.assertEqual(mock_llm.call_count, 2)

    def test_probe_sees_only_what_the_player_sees(self):
        probe = json.dumps({"culprit": "nobody", "confidence": 1, "reason": "-"})
        with patch("litellm.completion", side_effect=self._case_then(probe)) as mock_llm:
            _gm_tools()["generate_new_case"](language="Serbian", tool_context=_fake_context())
        material = mock_llm.call_args_list[1].kwargs["messages"][0]["content"]
        self.assertIn("Toksikološki nalaz", material)      # evidence is visible
        self.assertNotIn("POČINILAC", material)             # the killer brief is not
        self.assertNotIn("kradeš retka vina", material)     # nor are secrets
        self.assertNotIn("turning_point", material)

    def test_probe_failure_never_costs_the_player_a_game(self):
        case = json.dumps(copy.deepcopy(DEFAULT_CASE), ensure_ascii=False)
        with patch("litellm.completion",
                   side_effect=[self._llm_response(case), RuntimeError("probe model down")]):
            result = _gm_tools()["generate_new_case"](language="Serbian", tool_context=_fake_context())
        self.assertEqual(result["status"], "success")

    def test_unusable_revision_keeps_the_original_case(self):
        probe = json.dumps({"culprit": "dr Ana Simić", "confidence": 4, "reason": "obvious"})
        broken = json.dumps({"dossier": "", "evidence": [{"id": "x"}]})  # fails validation
        ctx = _fake_context()
        with patch("litellm.completion", side_effect=self._case_then(probe, broken)):
            result = _gm_tools()["generate_new_case"](language="Serbian", tool_context=ctx)
        self.assertEqual(result["status"], "success")
        self.assertEqual(ctx.state[STATE_KEY]["dossier"], DEFAULT_CASE["dossier"])
        self.assertEqual(len(ctx.state[STATE_KEY]["evidence"]), len(DEFAULT_CASE["evidence"]))

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
