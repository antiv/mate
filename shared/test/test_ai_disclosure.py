#!/usr/bin/env python3
"""
Tests for the Art. 50 "you are talking to an AI" notice.

The obligation has applied since 2 August 2026 and was not touched by the Digital
Omnibus that deferred the high-risk regime to December 2027. It bites hardest on
the widget: embedded in someone else's site and styled to match it, an agent
there is exactly the case where being an AI is *not* obvious.

The design decision under test is that there is no boolean. Disclosure is on
unless a waiver holds a reason, so the decision to switch it off and the record of
why cannot come apart — you cannot disable it and forget to say why, because they
are the same field.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.ai_disclosure import (
    DEFAULT_DISCLOSURE,
    disclosure_state,
    resolve_disclosure,
    validate_waiver,
)


class Row:
    """Stands in for an AgentConfig row; the helper accepts either form."""

    def __init__(self, ai_disclosure=None, ai_disclosure_waiver=None):
        self.ai_disclosure = ai_disclosure
        self.ai_disclosure_waiver = ai_disclosure_waiver


class TestResolveDisclosure(unittest.TestCase):

    def test_an_agent_that_was_never_configured_still_discloses(self):
        # The default has to be disclosure, not silence: an agent nobody thought
        # about is precisely the one that will end up embedded somewhere public.
        self.assertEqual(resolve_disclosure({}), DEFAULT_DISCLOSURE)
        self.assertEqual(resolve_disclosure(Row()), DEFAULT_DISCLOSURE)

    def test_custom_wording_replaces_the_default(self):
        self.assertEqual(
            resolve_disclosure({"ai_disclosure": "Razgovarate sa AI asistentom."}),
            "Razgovarate sa AI asistentom.")

    def test_blank_wording_is_not_a_way_to_switch_it_off(self):
        for blank in ("", "   ", "\n"):
            with self.subTest(blank=repr(blank)):
                self.assertEqual(resolve_disclosure({"ai_disclosure": blank}),
                                 DEFAULT_DISCLOSURE)

    def test_a_waiver_with_a_reason_turns_it_off(self):
        self.assertIsNone(resolve_disclosure(
            {"ai_disclosure_waiver": "Internal staff tool; disclosed at onboarding"}))

    def test_a_whitespace_waiver_does_not_turn_it_off(self):
        self.assertEqual(resolve_disclosure({"ai_disclosure_waiver": "   "}),
                         DEFAULT_DISCLOSURE)

    def test_a_waiver_beats_custom_wording(self):
        self.assertIsNone(resolve_disclosure(Row(
            ai_disclosure="Custom text",
            ai_disclosure_waiver="Obvious from context, this is a demo of the API")))


class TestValidateWaiver(unittest.TestCase):

    def test_no_waiver_is_the_normal_state_and_is_allowed(self):
        self.assertIsNone(validate_waiver(None))
        self.assertIsNone(validate_waiver(""))
        self.assertIsNone(validate_waiver("   "))

    def test_a_token_reason_is_refused(self):
        # "x" as a reason is switching it off silently in every way that matters.
        for thin in ("x", "n/a", "no", "-"):
            with self.subTest(reason=thin):
                self.assertIsNotNone(validate_waiver(thin))

    def test_a_real_reason_is_accepted(self):
        self.assertIsNone(validate_waiver(
            "Internal staff tool, users are told at onboarding"))


class TestDisclosureState(unittest.TestCase):
    """What a compliance record would report for an agent."""

    def test_a_disclosing_agent_reports_its_text_and_no_waiver(self):
        state = disclosure_state({})
        self.assertTrue(state["shown"])
        self.assertEqual(state["text"], DEFAULT_DISCLOSURE)
        self.assertIsNone(state["waiver_reason"])

    def test_a_waived_agent_reports_the_reason(self):
        state = disclosure_state({"ai_disclosure_waiver": "Staff only, told at onboarding"})
        self.assertFalse(state["shown"])
        self.assertIsNone(state["text"])
        self.assertEqual(state["waiver_reason"], "Staff only, told at onboarding")


if __name__ == "__main__":
    unittest.main()
