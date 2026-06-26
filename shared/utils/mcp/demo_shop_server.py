"""Demo e-commerce MCP server (stdio) — a fake shop for testing the Tier 3 sales agent
without a real Shopify/store account.

Run by an agent via mcp_servers_config:
    {"mcpServers": {"demo-shop": {"command": "python",
                                  "args": ["-m", "shared.utils.mcp.demo_shop_server"],
                                  "timeout": 60}}}

Exposes the same shape of operations a real e-commerce MCP would (search products, cart,
place order), so swapping to a real Shopify MCP later changes only the config, not the agent.
The cart is in-memory for the lifetime of this process (one trial agent).
"""

from typing import Dict, List

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo-shop")

_CURRENCY = "EUR"
_CATALOG: List[Dict] = [
    {"id": "tshirt-red", "name": "Red T-shirt", "price": 19.0, "category": "clothing",
     "description": "100% cotton red t-shirt.", "image": "https://picsum.photos/seed/tshirt-red/200"},
    {"id": "tshirt-blue", "name": "Blue T-shirt", "price": 19.0, "category": "clothing",
     "description": "100% cotton blue t-shirt.", "image": "https://picsum.photos/seed/tshirt-blue/200"},
    {"id": "hoodie-black", "name": "Black Hoodie", "price": 49.0, "category": "clothing",
     "description": "Warm fleece hoodie.", "image": "https://picsum.photos/seed/hoodie-black/200"},
    {"id": "mug-logo", "name": "Logo Mug", "price": 9.5, "category": "accessories",
     "description": "Ceramic mug, 350ml.", "image": "https://picsum.photos/seed/mug-logo/200"},
    {"id": "cap-classic", "name": "Classic Cap", "price": 14.0, "category": "accessories",
     "description": "Adjustable cotton cap.", "image": "https://picsum.photos/seed/cap-classic/200"},
    {"id": "sticker-pack", "name": "Sticker Pack", "price": 4.0, "category": "accessories",
     "description": "Set of 10 vinyl stickers.", "image": "https://picsum.photos/seed/sticker-pack/200"},
]
_BY_ID = {p["id"]: p for p in _CATALOG}
_cart: Dict[str, int] = {}


def _cart_view() -> Dict:
    items = []
    total = 0.0
    for pid, qty in _cart.items():
        p = _BY_ID.get(pid)
        if not p:
            continue
        line = round(p["price"] * qty, 2)
        total += line
        items.append({"id": pid, "name": p["name"], "price": p["price"], "quantity": qty, "line_total": line})
    return {"items": items, "currency": _CURRENCY, "total": round(total, 2), "count": sum(_cart.values())}


@mcp.tool()
def search_products(query: str = "") -> Dict:
    """Search the catalog. Empty query returns all products. Returns id, name, price, image."""
    q = (query or "").strip().lower()
    out = [p for p in _CATALOG if not q or q in p["name"].lower() or q in p["category"].lower() or q in p["description"].lower()]
    return {"currency": _CURRENCY, "products": out}


@mcp.tool()
def get_product(product_id: str) -> Dict:
    """Get a single product by id."""
    p = _BY_ID.get(product_id)
    return p or {"error": f"No product '{product_id}'"}


@mcp.tool()
def add_to_cart(product_id: str, quantity: int = 1) -> Dict:
    """Add a product to the cart by id. Returns the updated cart."""
    if product_id not in _BY_ID:
        return {"error": f"No product '{product_id}'. Use search_products first."}
    try:
        quantity = max(1, int(quantity))
    except (TypeError, ValueError):
        quantity = 1
    _cart[product_id] = _cart.get(product_id, 0) + quantity
    return {"status": "success", "cart": _cart_view()}


@mcp.tool()
def remove_from_cart(product_id: str) -> Dict:
    """Remove a product from the cart entirely. Returns the updated cart."""
    _cart.pop(product_id, None)
    return {"status": "success", "cart": _cart_view()}


@mcp.tool()
def view_cart() -> Dict:
    """Return the current cart items and total."""
    return _cart_view()


@mcp.tool()
def place_order(customer_name: str, customer_email: str, note: str = "") -> Dict:
    """Place the order for the current cart. Only call this AFTER the customer has explicitly
    confirmed they want to order. Clears the cart and returns an order id."""
    cart = _cart_view()
    if not cart["items"]:
        return {"error": "The cart is empty — add products before placing an order."}
    if not customer_name or not customer_email or "@" not in customer_email:
        return {"error": "A customer name and a valid email are required to place the order."}
    import uuid
    order_id = "DEMO-" + uuid.uuid4().hex[:8].upper()
    order = {"status": "success", "order_id": order_id, "customer_name": customer_name,
             "customer_email": customer_email, "items": cart["items"], "currency": cart["currency"],
             "total": cart["total"], "note": note or ""}
    _cart.clear()
    return order


if __name__ == "__main__":
    mcp.run()
