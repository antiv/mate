---
title: Website widget
summary: Put an agent on a website as a chat button, and control where it may be embedded.
audience: user
order: 90
covers:
  - server/widget_routes.py
  - templates/dashboard/modals/widget_keys_modal.html
  - static/js/modals/widget-keys.js
  - static/js/widget/mate-widget.js
  - static/js/widget/admin.js
  - templates/widget/admin.html
  - templates/widget/chat.html
---

# Website widget

The widget is a chat button you add to a website with one line of HTML. Visitors
click it and talk to one of your agents, without an account.

## Before you start

Check the agent on the **Agents** page:

- **Allowed Roles** must include `widget`. Visitors are anonymous and are given that
  role; an agent that does not list it refuses them. For example
  `["widget", "admin"]`.
- Remove the **Code Executor** tool. MATE refuses to load it on an agent that has a
  widget key, because visitors could use it to run commands on your server.
- Visitors are told they are talking to an AI. To change the wording, see
  *AI disclosure* in the [Agents guide](agents.md#ai-disclosure).

## Create a key and embed the widget

1. On the Agents page, click **Widget Keys** for the agent.
2. Optionally enter a **Label** and the **Allowed Origins**: the addresses of the
   sites that may embed the widget, separated by commas, such as
   `https://example.com, https://www.example.com`.
3. Click **Generate Key**.
4. Copy the **Embed Code** and paste it into the site's HTML, just before `</body>`:

```html
<script
  src="https://your-mate.example.com/widget/mate-widget.js"
  data-key="wk_..."
  data-server="https://your-mate.example.com"
></script>
```

A chat button appears in the corner of the page.

### Options on the snippet

| Attribute | Default | Effect |
|---|---|---|
| `data-position` | `bottom-right` | `bottom-right` or `bottom-left`. |
| `data-theme` | `auto` | `light`, `dark`, or `auto` to follow the visitor's system. |
| `data-button-color` | `#2563eb` | Colour of the button. |
| `data-button-text` | none | Text on the button instead of the icon. |
| `data-greeting` | none | A welcome message. |
| `data-width`, `data-height` | `400`, `600` | Size of the chat panel in pixels. |

The site's own scripts can open and close the chat with `MateWidget.open()`,
`MateWidget.close()` and `MateWidget.toggle()`.

## The widget admin panel

**Open Admin**, next to the embed code, opens a small admin page for this one
widget. It lets someone look after the widget without access to the whole
dashboard:

| Tab | What it changes |
|---|---|
| **Agent Settings** | The agent's description, model and instruction. |
| **Memory Blocks** | The blocks the agent reads. |
| **Files** | Documents the agent can search. |
| **Appearance** | Title, greeting, theme, colour, icon, whether visitors can attach files, and whether the page's address and title are passed to the agent. |

The admin page is opened with its own admin key, separate from the key in the embed
code. Treat that link as a password: whoever has it can rewrite the agent's
instruction.

## Limit where the widget works

**Allowed Origins is advisory unless your administrator switches enforcement on.**
By default, a request from a site that is not on the list is logged and still
answered. With `WIDGET_ORIGIN_STRICT=true` on the server, it is refused.

Even when enforced, this check relies on the visitor's browser. It stops your key
from being embedded on someone else's site; it does not stop a script from calling
the widget directly. To cap what such a caller can cost you, set
[rate limits](rate-limits.md).

To replace a key, generate a new one, update the snippet on your site, then delete
the old key.

## What visitors see when something fails

Visitors are shown a short, friendly message rather than the technical error: one
for a general failure, and one saying they do not have permission when the agent's
role check refuses them. Both follow the widget's language.

While you are setting a widget up, switch **Debug Mode** on for the agent to see the
real error text in the chat. Switch it off again before visitors arrive.

The button in the widget's header starts a new conversation. It asks for
confirmation, clears the chat and minimises the widget.

## If it does not work

| Symptom | Check |
|---|---|
| The button does not appear | The browser console for errors; that `data-key` and `data-server` are exact; that the MATE address is reachable from the visitor's network, over HTTPS if the site is HTTPS. |
| "Invalid widget key" | The key was deleted or deactivated, or was copied with a stray space. |
| The agent does not answer | The agent server is running (the status indicator at the top of the dashboard); the agent still exists and is a root agent. |
| "You do not have permission" | The agent's **Allowed Roles** does not include `widget`. |
| It works on one site but not another | With origin enforcement on, the other site is missing from the key's **Allowed Origins**. |
| It covers something on the page | Move it with `data-position`, or resize it with `data-width` and `data-height`. |

## Visitors and their conversations

Each visitor gets an anonymous id stored in their browser, and a user record with
the `widget` role, so they are recognised when they return in the same browser.
Records of visitors who have been inactive for five days are removed
automatically; see [Users and roles](users-and-roles.md#users-you-did-not-create).
