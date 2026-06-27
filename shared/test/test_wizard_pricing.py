#!/usr/bin/env python3
"""Unit tests for wizard pricing helpers (pure logic, no DB)."""

import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.wizard import pricing


class TestWizardPricingHelpers(unittest.TestCase):
    def test_is_empty_or_zero(self):
        # Empty/whitespace values
        self.assertTrue(pricing.is_empty_or_zero(""))
        self.assertTrue(pricing.is_empty_or_zero(None))
        self.assertTrue(pricing.is_empty_or_zero("   "))
        
        # Zeros in various formats
        self.assertTrue(pricing.is_empty_or_zero("0"))
        self.assertTrue(pricing.is_empty_or_zero("0.0"))
        self.assertTrue(pricing.is_empty_or_zero("0.00"))
        self.assertTrue(pricing.is_empty_or_zero("0 RSD"))
        self.assertTrue(pricing.is_empty_or_zero("€0"))
        self.assertTrue(pricing.is_empty_or_zero("0,00 RSD"))
        self.assertTrue(pricing.is_empty_or_zero("0.00 EUR"))
        self.assertTrue(pricing.is_empty_or_zero("RSD 0"))
        
        # Valid non-zero pricing
        self.assertFalse(pricing.is_empty_or_zero("49"))
        self.assertFalse(pricing.is_empty_or_zero("5.900"))
        self.assertFalse(pricing.is_empty_or_zero("12000 RSD"))
        self.assertFalse(pricing.is_empty_or_zero("€49"))
        
        # No digits at all should default to empty/zero -> Custom pricing
        self.assertTrue(pricing.is_empty_or_zero("Custom pricing"))
        self.assertTrue(pricing.is_empty_or_zero("RSD"))

    def test_format_with_from_prefix(self):
        # show_from = True
        self.assertEqual(pricing.format_with_from_prefix("5.900 RSD", "sr", True), "od 5.900 RSD")
        self.assertEqual(pricing.format_with_from_prefix("€49", "en", True), "from €49")
        
        # show_from = False
        self.assertEqual(pricing.format_with_from_prefix("5.900 RSD", "sr", False), "5.900 RSD")
        self.assertEqual(pricing.format_with_from_prefix("€49", "en", False), "€49")
        
        # Deduplication of existing prefixes
        self.assertEqual(pricing.format_with_from_prefix("od 5.900 RSD", "sr", True), "od 5.900 RSD")
        self.assertEqual(pricing.format_with_from_prefix("from €49", "en", True), "from €49")
        self.assertEqual(pricing.format_with_from_prefix("od 5.900 RSD", "sr", False), "5.900 RSD")
        self.assertEqual(pricing.format_with_from_prefix("from €49", "en", False), "€49")
        
        # Case insensitive deduplication
        self.assertEqual(pricing.format_with_from_prefix("Od 5.900 RSD", "sr", True), "od 5.900 RSD")
        self.assertEqual(pricing.format_with_from_prefix("From €49", "en", True), "from €49")

    def test_resolve_price(self):
        tier_priced = {"id": "tier1", "priced": True, "i18n": {"en": {"price": "from €49"}, "sr": {"price": "od 5.900 RSD"}}}
        cfg_valid = {
            "default_currency": "EUR",
            "prices": {"tier1": {"EUR": "€49", "RSD": "5.900 RSD"}},
            "show_from": {"tier1": True}
        }
        cfg_no_from = {
            "default_currency": "EUR",
            "prices": {"tier1": {"EUR": "€49", "RSD": "5.900 RSD"}},
            "show_from": {"tier1": False}
        }
        cfg_empty = {
            "default_currency": "EUR",
            "prices": {"tier1": {"EUR": "", "RSD": "0"}},
            "show_from": {"tier1": True}
        }
        
        # Test valid resolving with from
        self.assertEqual(pricing._resolve_price(tier_priced, "en", "EUR", cfg_valid), "from €49")
        self.assertEqual(pricing._resolve_price(tier_priced, "sr", "RSD", cfg_valid), "od 5.900 RSD")
        
        # Test valid resolving without from
        self.assertEqual(pricing._resolve_price(tier_priced, "en", "EUR", cfg_no_from), "€49")
        
        # Test custom pricing empty/zero fallback
        self.assertEqual(pricing._resolve_price(tier_priced, "en", "EUR", cfg_empty), "Custom pricing")


if __name__ == "__main__":
    unittest.main()
