#!/usr/bin/env python3
"""Unit tests for wizard partner key validation + origin allowlist (pure logic, no DB)."""

import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.utils.wizard import partners


class TestPartnerKey(unittest.TestCase):
    def test_valid_keys(self):
        for k in ["salon-a", "Salon_A1", "abc123"]:
            self.assertTrue(partners.valid_key(k))

    def test_invalid_keys(self):
        for k in ["", None, "has space", "bad/slash", "x" * 101, "emoji😀"]:
            self.assertFalse(partners.valid_key(k))


class TestOriginAllowlist(unittest.TestCase):
    def test_no_restriction_allows_any(self):
        self.assertTrue(partners.origin_allowed({"allowed_origins": []}, "https://anything.com/x"))
        self.assertTrue(partners.origin_allowed({"allowed_origins": []}, ""))

    def test_matching_origin_allowed(self):
        p = {"allowed_origins": ["https://salon-a.com"]}
        self.assertTrue(partners.origin_allowed(p, "https://salon-a.com/page?q=1"))

    def test_non_matching_origin_blocked(self):
        p = {"allowed_origins": ["https://salon-a.com"]}
        self.assertFalse(partners.origin_allowed(p, "https://evil.com/x"))
        self.assertFalse(partners.origin_allowed(p, ""))  # restricted but no referer

    def test_scheme_and_port_matter(self):
        p = {"allowed_origins": ["https://salon-a.com"]}
        self.assertFalse(partners.origin_allowed(p, "http://salon-a.com/x"))  # http != https


if __name__ == "__main__":
    unittest.main()
