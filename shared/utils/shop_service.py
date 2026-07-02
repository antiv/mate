"""Shared e-commerce logic for the native shop tools (``shop_tools``).

Holds everything that does NOT depend on where the cart is stored: catalog normalization,
cart computation, order building, DB persistence and order emails. ``shop_tools`` keeps the
cart in ``tool_context.state`` (per session), so the shop is multi-user safe.

Persistence and email are gated by the caller (only real deployments configure a partner key
and a vendor email), so wizard trial widgets neither store orders nor send mail.
"""

import json
import logging
import os
import smtplib
import uuid
from email.message import EmailMessage
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_CURRENCY = "EUR"
DEFAULT_SHOP_NAME = "Online Store"

FALLBACK_CATALOG: List[Dict] = [
    {"id": "tshirt-red",   "name": "Red T-shirt",  "price": 19.0, "category": "clothing",    "description": "100% cotton red t-shirt."},
    {"id": "tshirt-blue",  "name": "Blue T-shirt", "price": 19.0, "category": "clothing",    "description": "100% cotton blue t-shirt."},
    {"id": "hoodie-black", "name": "Black Hoodie", "price": 49.0, "category": "clothing",    "description": "Warm fleece hoodie."},
    {"id": "mug-logo",     "name": "Logo Mug",     "price":  9.5, "category": "accessories", "description": "Ceramic mug, 350ml."},
    {"id": "cap-classic",  "name": "Classic Cap",  "price": 14.0, "category": "accessories", "description": "Adjustable cotton cap."},
    {"id": "sticker-pack", "name": "Sticker Pack", "price":  4.0, "category": "accessories", "description": "Set of 10 vinyl stickers."},
]


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

def normalize_catalog(raw) -> List[Dict]:
    """Coerce a raw catalog (list of dicts, or a JSON-string of one) into a clean product list.

    Falls back to FALLBACK_CATALOG when nothing usable is provided. Each product gets a
    stable id, a numeric price (placeholder 9.99 when missing/invalid), category, description
    and a deterministic placeholder image.
    """
    if isinstance(raw, str):
        raw = raw.strip()
        try:
            raw = json.loads(raw) if raw else None
        except Exception:
            raw = None

    items = raw if isinstance(raw, list) else None
    catalog: List[Dict] = []
    if items:
        for item in items:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            try:
                price = round(float(item.get("price") or 0), 2)
            except (TypeError, ValueError):
                price = 0.0
            if price <= 0:
                price = 9.99
            pid = str(item.get("id") or item["name"]).strip().lower().replace(" ", "-")
            catalog.append({
                "id": pid,
                "name": str(item["name"]),
                "price": price,
                "category": str(item.get("category") or "product"),
                "description": str(item.get("description") or item["name"]),
                "image": item.get("image") or f"https://picsum.photos/seed/{pid}/200",
            })
        if catalog:
            return catalog

    return [{**p, "image": f"https://picsum.photos/seed/{p['id']}/200"} for p in FALLBACK_CATALOG]


def index_by_id(catalog: List[Dict]) -> Dict[str, Dict]:
    return {p["id"]: p for p in catalog}


def search(catalog: List[Dict], query: str = "") -> List[Dict]:
    q = (query or "").strip().lower()
    return [p for p in catalog
            if not q or q in p["name"].lower() or q in p["category"].lower()
            or q in p["description"].lower()]


# ---------------------------------------------------------------------------
# Cart
# ---------------------------------------------------------------------------

def cart_view(cart: Dict[str, int], by_id: Dict[str, Dict], currency: str) -> Dict:
    """Compute a cart view (line items + total) from a {product_id: quantity} mapping."""
    items = []
    total = 0.0
    for pid, qty in (cart or {}).items():
        p = by_id.get(pid)
        if not p:
            continue
        line = round(p["price"] * qty, 2)
        total += line
        items.append({"id": pid, "name": p["name"], "price": p["price"],
                      "quantity": qty, "line_total": line})
    return {"items": items, "currency": currency, "total": round(total, 2),
            "count": sum((cart or {}).values())}


# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------

def build_order(view: Dict, customer_name: str, customer_email: str, note: str = "") -> Dict:
    """Build an order dict from a cart view. Caller must validate the cart is non-empty first."""
    return {
        "status": "success",
        "order_id": "ORD-" + uuid.uuid4().hex[:8].upper(),
        "customer_name": customer_name,
        "customer_email": customer_email,
        "items": view["items"],
        "currency": view["currency"],
        "total": view["total"],
        "note": note or "",
    }


def persist_order(order: Dict, partner_key: str, shop_name: str) -> bool:
    """Store the order in the shop_orders table. Returns True on success, False if skipped/failed."""
    try:
        from shared.utils.database_client import get_database_client
        from shared.utils.models import ShopOrder
    except Exception as exc:
        logger.warning("Order persistence unavailable (import failed): %s", exc)
        return False

    session = None
    try:
        db = get_database_client()
        session = db.get_session()
        if not session:
            return False
        row = ShopOrder(
            order_id=order["order_id"],
            partner_key=partner_key or None,
            shop_name=shop_name,
            customer_name=order["customer_name"],
            customer_email=order["customer_email"],
            currency=order["currency"],
            total=order["total"],
            note=order.get("note") or None,
            status="new",
        )
        row.set_items(order["items"])
        session.add(row)
        session.commit()
        return True
    except Exception as exc:
        logger.warning("Failed to persist order %s: %s", order.get("order_id"), exc)
        if session:
            session.rollback()
        return False
    finally:
        if session:
            session.close()


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _send_email(to: str, subject: str, body: str) -> bool:
    """Send a plain-text email via the platform SMTP env vars. False if skipped/failed."""
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    if not smtp_host or not to:
        return False
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    from_addr = os.environ.get("SMTP_FROM", smtp_user) or smtp_user
    try:
        msg = EmailMessage()
        msg["From"] = from_addr
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.starttls()
            if smtp_user and smtp_pass:
                s.login(smtp_user, smtp_pass)
            s.send_message(msg)
        return True
    except Exception as exc:
        logger.warning("Failed to send email to %s: %s", to, exc)
        return False


def _order_lines(items: List[Dict], currency: str) -> str:
    return "\n".join(
        f"  - {it['name']} × {it['quantity']}  =  {it['line_total']} {currency}" for it in items
    )


def send_order_emails(order: Dict, vendor_email: str, shop_name: str) -> Dict:
    """Email the vendor and the customer about a placed order. Returns {vendor, customer} bools.

    Callers should only invoke this for real deployments (vendor_email configured); the
    wizard trial leaves it unset so test orders never trigger mail.
    """
    lines = _order_lines(order["items"], order["currency"])
    name = shop_name or DEFAULT_SHOP_NAME

    vendor_body = (
        f"New order received at {name}\n"
        f"{'=' * 48}\n"
        f"Order ID : {order['order_id']}\n"
        f"Customer : {order['customer_name']}\n"
        f"Email    : {order['customer_email']}\n"
        f"{'-' * 48}\n"
        f"{lines}\n"
        f"{'-' * 48}\n"
        f"Total    : {order['total']} {order['currency']}\n"
    )
    if order.get("note"):
        vendor_body += f"Note     : {order['note']}\n"
    vendor_body += "\nPlease contact the customer to confirm and arrange delivery."
    vendor_sent = _send_email(vendor_email, f"[{name}] New order {order['order_id']}", vendor_body)

    customer_body = (
        f"Thank you for your order at {name}!\n"
        f"{'=' * 48}\n"
        f"Order ID : {order['order_id']}\n"
        f"{'-' * 48}\n"
        f"{lines}\n"
        f"{'-' * 48}\n"
        f"Total    : {order['total']} {order['currency']}\n"
    )
    if order.get("note"):
        customer_body += f"Note     : {order['note']}\n"
    customer_body += (
        "\nWe have received your order and will contact you shortly to confirm "
        "details and arrange delivery.\n\nThank you for shopping with us!"
    )
    customer_sent = _send_email(order["customer_email"],
                                f"[{name}] Order confirmation {order['order_id']}", customer_body)

    return {"vendor": vendor_sent, "customer": customer_sent}
