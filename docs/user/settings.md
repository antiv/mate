---
title: Settings
summary: Server-wide defaults an admin sets in the dashboard, the default image model and model prices.
audience: user
order: 165
covers:
  - templates/dashboard/settings.html
  - static/js/settings-page.js
  - shared/utils/system_settings.py
  - shared/utils/model_pricing.py
  - shared/utils/dashboard/dashboard_server.py::*setting*
  - shared/utils/dashboard/dashboard_server.py::*price*
---

# Settings

**Control Room → Settings** holds server-wide defaults. Only admins can open it. A
value set here wins over the matching environment variable; **Clear** removes it, and
the environment variable (or the built-in default) applies again. Every change is
recorded in the [audit log](usage-and-audit.md) as `config.change`, with the old and
the new value.

## Default image model

The image model for agents that have **Image Tools** on but no model of their own.

1. Type a model, or pick one from the suggestions. Any image model LiteLLM supports
   works, written as `provider/model`: `gemini/gemini-2.5-flash-image`,
   `openrouter/google/gemini-2.5-flash-image`, `black_forest_labs/flux-pro-1.1`,
   `gpt-image-1`, and so on.
2. **Save**. A name LiteLLM cannot place is refused with a message.

Below the field:

- **In use** is the model agents get now, and where it comes from: this page, the
  `IMAGE_MODEL` environment variable, or the built-in default `dall-e-3`.
- **API key** says whether the server has the key for that model's provider, for
  example `OPENAI_API_KEY` or `BFL_API_KEY`. It only checks that the key is set. A
  wrong key shows up on the first image.

The change applies to the next image an agent generates; no restart is needed.
An agent that names its own model in its tool settings keeps using that one.

## Model prices

Every model call is priced in US dollars when it is logged, and the
[Usage page](usage-and-audit.md#cost) adds those prices up. The price per token
comes from, in order:

1. a price set on this page;
2. for `openrouter/` models, OpenRouter's own price list, which MATE fetches once a
   day (only if an `openrouter/` model is used);
3. LiteLLM's price list.

A model none of them covers has **no price**: its calls are left out of the cost,
and the Usage page shows how much of the traffic that is. This is never shown as
$0. LiteLLM reports 0 for models it does not know, so MATE does not treat its 0 as a
price. Typical models without a price are a self-hosted model (Ollama, vLLM), an
Ollama cloud model, or a provider's newest model before the lists catch up.

The table lists every model that was used, with its calls, the calls without a
price, the price in use per million tokens (input / output) and where it comes from.

- **Set by hand.** Enter the input and output price per million tokens and
  **Save**. Use 0 / 0 for a model that costs nothing, such as one you host yourself.
  A price set here also replaces a listed one. Saving reprices **all** the model's
  calls, past ones included.
- **Remove** drops the manual price. The model's calls are priced from the lists
  again, or have no price.
- **Another model** adds a price for a model not used yet.
- **Fill in missing costs** prices the calls logged without a cost whose model has a
  price now, for example calls made before OpenRouter's list was fetched. Calls that
  already have a cost keep it.

Prices are list prices. Discounts for cached input are not applied (the logs do not
record cached tokens), nor are the higher prices some providers charge for very long
prompts.

Setting and removing a price are recorded in the audit log as `config.change` on
`model_price`, with the old and the new price.
