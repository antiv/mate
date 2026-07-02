"""Native e-commerce tools for agents — a multi-user shop.

Unlike the demo_shop MCP server (one shared subprocess cart), these are in-process ADK
function tools whose cart lives in ``tool_context.state``. State is session-scoped and
persisted by the session service, so every visitor of a public widget gets their own cart
that survives across turns and never collides with another visitor's.

Enabled via tool_config:
    {
      "shop": {
        "catalog": [{"id": "...", "name": "...", "price": 1500, "category": "...", "description": "..."}],
        "currency": "RSD",
        "shop_name": "Salon Lena",
        "vendor_email": "orders@salon.rs",   # optional — when set, place_order emails vendor + customer
        "partner_key": "salon-lena"          # optional — when set, orders are persisted to shop_orders
      }
    }

Both vendor_email and partner_key are optional and independent: leave them unset (e.g. wizard
trials) and place_order succeeds without sending mail or storing anything.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from google.adk.tools.tool_context import ToolContext

from shared.utils import shop_service

logger = logging.getLogger(__name__)

_CART_KEY = "shop_cart"


def create_shop_tools_from_config(config: Dict[str, Any]) -> List[Any]:
    """Build the shop tools if tool_config has a "shop" block, bound to its catalog + settings."""
    tool_config = config.get("tool_config")
    if isinstance(tool_config, str):
        try:
            tool_config = json.loads(tool_config)
        except json.JSONDecodeError:
            tool_config = {}
    tool_config = tool_config or {}

    shop_cfg = tool_config.get("shop")
    if not shop_cfg:
        return []
    cfg = shop_cfg if isinstance(shop_cfg, dict) else {}

    currency = (cfg.get("currency") or shop_service.DEFAULT_CURRENCY).strip().upper()
    shop_name = (cfg.get("shop_name") or shop_service.DEFAULT_SHOP_NAME).strip()
    vendor_email = (cfg.get("vendor_email") or "").strip()
    partner_key = (cfg.get("partner_key") or "").strip()
    catalog = shop_service.normalize_catalog(cfg.get("catalog"))
    by_id = shop_service.index_by_id(catalog)

    def _get_cart(tool_context: Optional[ToolContext]) -> Dict[str, int]:
        if not tool_context or not getattr(tool_context, "state", None):
            return {}
        return dict(tool_context.state.get(_CART_KEY) or {})

    def _set_cart(tool_context: Optional[ToolContext], cart: Dict[str, int]) -> None:
        # Reassign the whole dict so ADK records the state delta and persists it.
        if tool_context and getattr(tool_context, "state", None) is not None:
            tool_context.state[_CART_KEY] = cart

    def search_products(query: str = "", tool_context: ToolContext = None) -> Dict:
        """Search the catalog. Empty query returns all products. Returns id, name, price, image."""
        return {"currency": currency, "products": shop_service.search(catalog, query)}

    def get_product(product_id: str, tool_context: ToolContext = None) -> Dict:
        """Get a single product by id."""
        return by_id.get(product_id) or {"error": f"No product '{product_id}'"}

    def add_to_cart(product_id: str, quantity: int = 1, tool_context: ToolContext = None) -> Dict:
        """Add a product to the current visitor's cart by id. Returns the updated cart."""
        if product_id not in by_id:
            return {"error": f"No product '{product_id}'. Use search_products first."}
        try:
            quantity = max(1, int(quantity))
        except (TypeError, ValueError):
            quantity = 1
        cart = _get_cart(tool_context)
        cart[product_id] = cart.get(product_id, 0) + quantity
        _set_cart(tool_context, cart)
        return {"status": "success", "cart": shop_service.cart_view(cart, by_id, currency)}

    def remove_from_cart(product_id: str, tool_context: ToolContext = None) -> Dict:
        """Remove a product from the current visitor's cart entirely. Returns the updated cart."""
        cart = _get_cart(tool_context)
        cart.pop(product_id, None)
        _set_cart(tool_context, cart)
        return {"status": "success", "cart": shop_service.cart_view(cart, by_id, currency)}

    def view_cart(tool_context: ToolContext = None) -> Dict:
        """Return the current visitor's cart items and total."""
        return shop_service.cart_view(_get_cart(tool_context), by_id, currency)

    def place_order(customer_name: str, customer_email: str, note: str = "",
                    tool_context: ToolContext = None) -> Dict:
        """Place the order for the current visitor's cart. Only call this AFTER the customer has
        explicitly confirmed. Clears the cart, persists/emails when configured, returns an order id."""
        cart = _get_cart(tool_context)
        view = shop_service.cart_view(cart, by_id, currency)
        if not view["items"]:
            return {"error": "The cart is empty — add products before placing an order."}
        if not customer_name or not customer_email or "@" not in customer_email:
            return {"error": "A customer name and a valid email are required to place the order."}

        order = shop_service.build_order(view, customer_name, customer_email, note)
        _set_cart(tool_context, {})  # clear this visitor's cart

        if partner_key:
            shop_service.persist_order(order, partner_key, shop_name)

        emails_sent = {"vendor": False, "customer": False}
        if vendor_email:
            emails_sent = shop_service.send_order_emails(order, vendor_email, shop_name)

        return {**order, "emails_sent": emails_sent}

    return [search_products, get_product, add_to_cart, remove_from_cart, view_cart, place_order]
