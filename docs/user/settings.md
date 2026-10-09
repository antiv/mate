---
title: Settings
summary: Server-wide defaults an admin sets in the dashboard, starting with the default image model.
audience: user
order: 165
covers:
  - templates/dashboard/settings.html
  - static/js/settings-page.js
  - shared/utils/system_settings.py
  - shared/utils/dashboard/dashboard_server.py::*setting*
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
