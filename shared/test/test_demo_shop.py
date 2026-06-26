#!/usr/bin/env python3
"""Unit tests for the demo e-commerce MCP server tools (Tier 3 testing backend)."""

import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import shared.utils.mcp.demo_shop_server as shop


class TestDemoShop(unittest.TestCase):
    def setUp(self):
        shop._cart.clear()

    def test_search_all_and_query(self):
        self.assertTrue(len(shop.search_products("")["products"]) >= 5)
        ids = [p["id"] for p in shop.search_products("shirt")["products"]]
        self.assertIn("tshirt-red", ids)
        self.assertNotIn("mug-logo", ids)

    def test_add_and_view_cart(self):
        shop.add_to_cart("tshirt-red", 2)
        shop.add_to_cart("mug-logo")
        cart = shop.view_cart()
        self.assertEqual(cart["count"], 3)
        self.assertEqual(cart["total"], round(19.0 * 2 + 9.5, 2))

    def test_add_invalid_product(self):
        self.assertIn("error", shop.add_to_cart("nope"))

    def test_remove_from_cart(self):
        shop.add_to_cart("cap-classic")
        shop.remove_from_cart("cap-classic")
        self.assertEqual(shop.view_cart()["count"], 0)

    def test_place_order_success_and_clears_cart(self):
        shop.add_to_cart("hoodie-black")
        order = shop.place_order("Pera Peric", "pera@example.com")
        self.assertEqual(order["status"], "success")
        self.assertTrue(order["order_id"].startswith("DEMO-"))
        self.assertEqual(order["total"], 49.0)
        self.assertEqual(shop.view_cart()["count"], 0)  # cart cleared

    def test_place_order_empty_cart(self):
        self.assertIn("error", shop.place_order("X", "x@y.com"))

    def test_place_order_requires_valid_email(self):
        shop.add_to_cart("mug-logo")
        self.assertIn("error", shop.place_order("X", "not-an-email"))


if __name__ == "__main__":
    unittest.main()
