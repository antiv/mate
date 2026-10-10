"""
What a model call costs, in US dollars.

Every logged call gets a cost from its model's price per token. Prices come
from, in order:

1. a price an admin set on the Settings page (model_prices table), for models
   no published list covers: a self-hosted model, a provider LiteLLM has not
   mapped;
2. for openrouter/* models, OpenRouter's own price list (its public /models
   endpoint), since LiteLLM has no entry for most of them;
3. LiteLLM's price table.

A model none of them prices has no cost (None), which is not the same as free.
LiteLLM reports 0 for models it does not know, so its 0 counts as no price; an
admin who wants a model treated as free sets 0 on the Settings page.

The cost is what the provider's list price says, without discounts for cached
input (the logs do not record cached tokens) and without tiered prices for very
long prompts.
"""

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

OPENROUTER_PREFIX = "openrouter/"
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_REFRESH_SECONDS = 24 * 3600
OPENROUTER_RETRY_SECONDS = 3600
MANUAL_CACHE_SECONDS = 60

SOURCE_MANUAL = "manual"
SOURCE_OPENROUTER = "openrouter"
SOURCE_LITELLM = "litellm"


@dataclass(frozen=True)
class Price:
    """A model's price in US dollars per token, and where it came from."""
    input_per_token: float
    output_per_token: float
    source: str


# --- Manual prices ------------------------------------------------------------
# Read from the database, cached briefly: calls are logged by the agent server,
# prices are set on the dashboard, a separate process this one cannot notify.

_manual: Dict[str, Price] = {}
_manual_loaded_at = 0.0
_manual_lock = threading.Lock()


def _manual_prices() -> Dict[str, Price]:
    global _manual, _manual_loaded_at
    with _manual_lock:
        if time.monotonic() - _manual_loaded_at < MANUAL_CACHE_SECONDS and _manual_loaded_at:
            return _manual
        prices: Dict[str, Price] = {}
        try:
            from .database_client import get_database_client
            from .models import ModelPrice
            session = get_database_client().get_session()
            if session is not None:
                try:
                    for row in session.query(ModelPrice).all():
                        prices[row.model_name] = Price(row.input_usd_per_mtok / 1e6,
                                                       row.output_usd_per_mtok / 1e6, SOURCE_MANUAL)
                finally:
                    session.close()
        except Exception as e:
            logger.warning(f"Could not read model prices: {e}")
            return _manual
        _manual, _manual_loaded_at = prices, time.monotonic()
        return _manual


def forget_manual_prices() -> None:
    """Re-read manual prices on next use, after this process changed them."""
    global _manual_loaded_at
    with _manual_lock:
        _manual_loaded_at = 0.0


# --- OpenRouter ---------------------------------------------------------------
# Fetched in the background, never while a call is being logged: until the list
# arrives, an openrouter model has no price, and the Settings page can fill the
# missing costs in afterwards.

_openrouter: Dict[str, Price] = {}
_openrouter_fetched_at = 0.0
_openrouter_attempted_at = 0.0
_openrouter_lock = threading.Lock()


def _parse_openrouter(payload: Dict[str, Any]) -> Dict[str, Price]:
    prices: Dict[str, Price] = {}
    for model in payload.get("data") or []:
        try:
            pricing = model.get("pricing") or {}
            prompt, completion = float(pricing["prompt"]), float(pricing["completion"])
        except (KeyError, TypeError, ValueError):
            continue
        # A router such as openrouter/auto lists -1: its price depends on the model it picks
        if prompt < 0 or completion < 0 or not model.get("id"):
            continue
        prices[model["id"]] = Price(prompt, completion, SOURCE_OPENROUTER)
    return prices


def _fetch_openrouter() -> None:
    global _openrouter, _openrouter_fetched_at
    try:
        import httpx
        response = httpx.get(OPENROUTER_MODELS_URL, timeout=20)
        response.raise_for_status()
        prices = _parse_openrouter(response.json())
    except Exception as e:
        logger.warning(f"Could not fetch OpenRouter's model prices: {e}")
        return
    with _openrouter_lock:
        _openrouter, _openrouter_fetched_at = prices, time.monotonic()
    logger.info(f"Fetched OpenRouter prices for {len(prices)} models")


def _openrouter_prices(wait: bool = False) -> Dict[str, Price]:
    """OpenRouter's prices, starting a refresh when they are missing or old.

    `wait` fetches in this thread instead, for the dashboard's own requests.
    """
    global _openrouter_attempted_at
    now = time.monotonic()
    with _openrouter_lock:
        stale = not _openrouter_fetched_at or now - _openrouter_fetched_at > OPENROUTER_REFRESH_SECONDS
        due = stale and (not _openrouter_attempted_at or now - _openrouter_attempted_at > OPENROUTER_RETRY_SECONDS)
        if due:
            _openrouter_attempted_at = now
    if due:
        if wait:
            _fetch_openrouter()
        else:
            threading.Thread(target=_fetch_openrouter, name="openrouter-prices", daemon=True).start()
    return _openrouter


# --- LiteLLM ------------------------------------------------------------------

def _litellm_price(model_name: str) -> Optional[Price]:
    try:
        import litellm
        info = litellm.get_model_info(model_name)
    except Exception:
        return None
    prompt = info.get("input_cost_per_token") or 0
    completion = info.get("output_cost_per_token") or 0
    # 0 is what LiteLLM reports for a model it has no price for
    if prompt <= 0 and completion <= 0:
        return None
    return Price(float(prompt), float(completion), SOURCE_LITELLM)


# --- Lookup -------------------------------------------------------------------

def price_for(model_name: Optional[str], wait: bool = False) -> Optional[Price]:
    """The model's price per token, or None when no source has one."""
    if not model_name:
        return None
    manual = _manual_prices().get(model_name)
    if manual is not None:
        return manual
    if model_name.startswith(OPENROUTER_PREFIX):
        price = _openrouter_prices(wait).get(model_name[len(OPENROUTER_PREFIX):])
        if price is not None:
            return price
    return _litellm_price(model_name)


def output_tokens(model_name: Optional[str], response_tokens: Optional[int],
                  thoughts_tokens: Optional[int]) -> int:
    """Billed output tokens: the response, plus thinking where it is counted apart.

    Gemini, called natively by ADK, reports thinking separately from the
    response, and bills it as output. Through LiteLLM, the response count
    already includes reasoning, which ADK also reports as thinking, so adding it
    would count it twice. LangGraph calls every model through LiteLLM and logs
    no thinking count.
    """
    total = response_tokens or 0
    if model_name and thoughts_tokens:
        from .utils import _is_gemini_model
        if _is_gemini_model(model_name):
            total += thoughts_tokens
    return total


def cost_usd(model_name: Optional[str], prompt_tokens: Optional[int], response_tokens: Optional[int],
             thoughts_tokens: Optional[int] = None, tool_use_tokens: Optional[int] = None,
             wait: bool = False) -> Optional[float]:
    """What a call cost in US dollars, or None when its model has no known price."""
    price = price_for(model_name, wait)
    if price is None:
        return None
    return _cost(price, model_name, prompt_tokens, response_tokens, thoughts_tokens, tool_use_tokens)


def _cost(price: Price, model_name: Optional[str], prompt_tokens: Optional[int],
          response_tokens: Optional[int], thoughts_tokens: Optional[int],
          tool_use_tokens: Optional[int]) -> float:
    # Tool-use prompt tokens (Gemini) are counted apart from the prompt and billed as input
    tokens_in = (prompt_tokens or 0) + (tool_use_tokens or 0)
    tokens_out = output_tokens(model_name, response_tokens, thoughts_tokens)
    return tokens_in * price.input_per_token + tokens_out * price.output_per_token


# --- Filling in and listing ----------------------------------------------------

def recompute_costs(session, model_name: Optional[str] = None, only_missing: bool = True) -> int:
    """Price logged calls again; returns how many got a cost.

    `only_missing` fills in calls logged before their model had a price.
    Otherwise every call (of `model_name`, if given) is priced at today's price,
    which is what an admin asks for when setting a model's price by hand.
    """
    from .models import TokenUsageLog
    query = session.query(TokenUsageLog.model_name).filter(TokenUsageLog.status == "SUCCESS")
    if model_name:
        query = query.filter(TokenUsageLog.model_name == model_name)
    if only_missing:
        query = query.filter(TokenUsageLog.cost_usd.is_(None))
    names = [row[0] for row in query.distinct().all() if row[0]]

    priced = 0
    for name in names:
        price = price_for(name, wait=True)
        rows = session.query(TokenUsageLog).filter(TokenUsageLog.status == "SUCCESS",
                                                   TokenUsageLog.model_name == name)
        if only_missing:
            rows = rows.filter(TokenUsageLog.cost_usd.is_(None))
        for row in rows.yield_per(500):
            row.cost_usd = (None if price is None else
                            _cost(price, name, row.prompt_tokens, row.response_tokens,
                                  row.thoughts_tokens, row.tool_use_tokens))
            if row.cost_usd is not None:
                priced += 1
    session.commit()
    return priced


def model_price_rows(session) -> List[Dict[str, Any]]:
    """Every model that was used or has a manual price, with its price and how much is unpriced."""
    from sqlalchemy import case, func
    from .models import ModelPrice, TokenUsageLog

    used = session.query(
        TokenUsageLog.model_name,
        func.count(TokenUsageLog.id),
        func.sum(TokenUsageLog.cost_usd),
        func.sum(case((TokenUsageLog.cost_usd.is_(None), 1), else_=0)),
    ).filter(TokenUsageLog.status == "SUCCESS", TokenUsageLog.model_name.isnot(None)) \
        .group_by(TokenUsageLog.model_name).all()

    manual = {row.model_name: row for row in session.query(ModelPrice).all()}
    rows: Dict[str, Dict[str, Any]] = {}
    for name, calls, cost, unpriced in used:
        rows[name] = {"model_name": name, "calls": int(calls or 0), "cost_usd": float(cost or 0),
                      "unpriced_calls": int(unpriced or 0)}
    for name in manual:
        rows.setdefault(name, {"model_name": name, "calls": 0, "cost_usd": 0.0, "unpriced_calls": 0})

    for name, row in rows.items():
        price = price_for(name, wait=True)
        row["source"] = price.source if price else None
        row["input_usd_per_mtok"] = price.input_per_token * 1e6 if price else None
        row["output_usd_per_mtok"] = price.output_per_token * 1e6 if price else None
        m = manual.get(name)
        row["manual"] = ({"input_usd_per_mtok": m.input_usd_per_mtok,
                          "output_usd_per_mtok": m.output_usd_per_mtok} if m else None)
    return sorted(rows.values(), key=lambda r: (-r["calls"], r["model_name"]))
