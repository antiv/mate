# MATE Agent Builder Wizard

An embeddable, step-by-step wizard that lets prospects build, **test**, and request an AI agent
straight from your marketing site. The wizard provisions a live **trial agent** the visitor can
chat with, then captures their contact details as a lead. There is **no billing** — the wizard
ends with a price estimate and a "contact us" instruction; you follow up to activate.

---

## Quick Start

Add the wizard to any page as an inline iframe with a single `<script>` tag:

```html
<div id="mate-wizard"></div>
<script
  src="https://your-mate-instance.com/wizard/mate-wizard.js"
  data-server="https://your-mate-instance.com"
  data-target="mate-wizard"
></script>
```

The wizard renders inside `#mate-wizard` and auto-resizes. You can also embed it directly:

```html
<iframe src="https://your-mate-instance.com/wizard/embed" style="width:100%;height:720px;border:0"></iframe>
```

---

## Loader Options

Configuration is done via `data-*` attributes on the script tag:

| Attribute | Required | Default | Description |
|---|---|---|---|
| `data-server` | Yes | — | URL of your MATE instance (e.g. `https://mate.example.com`) |
| `data-target` | No | (script's parent) | `id` of the element to render the wizard into |
| `data-tier` | No | — | Pre-select a tier (`tier1`…`tier4`), skipping the chooser |
| `data-lang` | No | `en` | UI language (`en` or `sr`) |
| `data-currency` | No | configured default | Price currency to display (e.g. `EUR`, `RSD`) |
| `data-contact-email` | No | `WIZARD_CONTACT_EMAIL` | Override the contact email shown at the end |
| `data-partner` | No | — | Partner/site key — selects that site's pricing, allowed origins and contact |
| `data-height` | No | `720` | Initial iframe height in pixels (auto-grows via `postMessage`) |

---

## Partners (per-site pricing & origins)

Each embedding site can be a **partner** with its own prices, allowed domains and contact email,
managed at **Dashboard → Wizard Pricing** (pick a partner in the "Site / partner" selector, or create one).
The site embeds with `data-partner="<key>"`:

- **Prices** for that partner override the global default (which overrides the seed defaults).
- **Allowed origins**: if set, the wizard only renders (and provisions) when embedded on one of those
  domains — enforced on `/embed`, `/api/session/start` and `/api/session/provision` via the request
  `Referer`/`Origin` (legitimate in-iframe API calls, which carry the MATE host, are allowed). Returns 403
  otherwise. Caveat: a partner site that sends no `Referer` (strict `Referrer-Policy: no-referrer`) can't be
  origin-verified — leave its allowed origins empty to permit it. This is browser-honored gating, not a
  cryptographic control (no billing), backed by rate limits.
- **Contact email** for the partner is used on the final screen.
- **Leads** record which `partner_key` they came from (shown as a column on Wizard Leads).

## Tiers & pricing

Tier **labels/descriptions/features** (localized en/sr) live in
[`shared/utils/wizard/pricing.py`](../shared/utils/wizard/pricing.py). Tier **prices** (per currency)
and the **default currency** are stored in the database and edited from the dashboard at
**Dashboard → Wizard Pricing** (`/dashboard/wizard-pricing`) — no code change or restart. Prices are
display-only (there is no billing); the shown estimate is snapshotted onto the lead.

The embedding site chooses which currency to display via `data-currency` (e.g. `EUR`, `RSD`); when
omitted, the configured default currency is used. Add a new currency right in the pricing editor.

A tier offers a **live trial** only if its agent template exists; otherwise it collects a structured
request as a lead.

| Tier | What it does | Trial provisioned | Template |
|---|---|---|---|
| **tier1** — Website Support | Reads the prospect's website (via the `browser` tool) to answer support questions | ✅ | `wizard-tier1-website-support.json` |
| **tier2** — Support + Scheduling | tier1 + Google Calendar (availability / booking) | ✅ (needs a Google service account configured) | `wizard-tier2-support-scheduling.json` |
| **tier3** — Sales | Shows products, fills a cart and places orders via an e-commerce MCP | ✅ (trial uses a built-in demo shop) | `wizard-tier3-sales.json` |
| **tier4** — Custom | Pick MATE capabilities of interest + describe the need → forwarded as a lead (no trial) | ✗ (lead only) | — |

Trial tier templates live in [`templates/agent_templates/`](../templates/agent_templates/) and reuse the
[Template Library](TEMPLATE_LIBRARY.md). Per-prospect values are injected into `{{PLACEHOLDER}}`
tokens at provision time (e.g. `{{SITE_URL}}`, `{{EXTRA_INSTRUCTIONS}}`,
`{{STORE_DOMAIN}}`, `{{STORE_TOKEN}}`).

---

## Site analysis (tailored instructions)

When a tier with a website is provisioned, MATE crawls the site and runs an LLM
(`WIZARD_ANALYSIS_MODEL`) to extract a short **business description** and a list of **services /
appointment reasons**. These are injected into the trial agent:

- the agent's **description** field becomes the generated business summary (a starting point you can edit);
- the **instruction** gets the services list — e.g. a Tier 2 scheduling agent offers those services as
  booking reasons instead of asking open-ended, and a Tier 1 agent uses them as business context.

Analysis is best-effort: if the LLM is unavailable, the agent falls back to generic instructions.

## How a Trial Works

1. The visitor picks a tier and fills in the config (e.g. website URL + instructions).
2. MATE provisions an isolated project named `trial_<tier>_<token>`, creates the agent(s) from the
   tier template, and issues a `wk_…` **widget key**.
3. The wizard embeds the standard [widget chat](WIDGET_INTEGRATION.md) so the visitor can test the
   agent immediately.
4. The visitor submits their details → a **lead** is stored (with a snapshot of the price estimate
   and a reference to the trial).
5. Trials are disposable: a daily job removes expired ones (see **Cleanup** below). Leads are kept.

---

## Managing Leads

Open the **MATE Dashboard → Wizard Leads** (`/dashboard/wizard-leads`, admin only) to:

- See every lead (name, email, company, tier, estimated price, created date).
- Open the lead's trial chat (while the trial is still alive).
- Move a lead through `new → contacted → converted → archived`.

---

## Configuration (Environment Variables)

| Variable | Default | Purpose |
|---|---|---|
| `WIZARD_CONTACT_EMAIL` | `sales@example.com` | Contact address shown on the final wizard screen and returned to the lead |
| `WIZARD_CURRENCY` | `EUR` | Default price currency when the embed doesn't pass `data-currency` (overridable per-tier in the pricing editor) |
| `WIZARD_SHOP_CURRENCY` | `EUR` | Fallback currency for the Tier 3 trial shop catalog when site analysis doesn't detect one |
| `GOOGLE_SERVICE_ACCOUNT_INFO` | — | Service-account JSON used by the Tier 2 Google Calendar tool (shared with the Drive integration) |
| `WIZARD_DEMO_CALENDAR_ID` | SA `primary` | Calendar id used by Tier 2 **trial** agents (demo free/busy + booking) |
| `WIZARD_CALENDAR_TIMEZONE` | `Europe/Belgrade` | Timezone for events created by the calendar tool |
| `WIZARD_TRIAL_TTL_DAYS` | `7` | How long a trial agent lives before cleanup |
| `WIZARD_TIER3_TRIAL_TTL_DAYS` | `2` | Shorter TTL for Tier 3 (sales) trials |
| `WIZARD_SESSION_IDLE_HOURS` | `4` | Hours of inactivity after which a provisioned trial is eagerly cleaned up |
| `WIZARD_TRIAL_MAX_PAGES` | `5` | Max pages crawled per trial provision (separate from `WIZARD_CRAWL_MAX_PAGES`) |
| `WIZARD_CLEANUP_ENABLED` | `true` | Enable the daily trial cleanup job |
| `WIZARD_CAPTCHA_PROVIDER` | (unset) | When set, enables the captcha hook on provisioning (pluggable; off by default) |

### Tier 2 calendar setup

The Tier 2 agent reaches a calendar via a Google **service account** (no per-user OAuth):

1. Create a service account in Google Cloud, enable the Calendar API, and put its JSON in `GOOGLE_SERVICE_ACCOUNT_INFO`.
2. **Trials** need no calendar at all — the Tier 2 trial template sets `"demo": true`, so the calendar tools run in **demo mode**: an in-process simulated calendar (a seeded daily 13:00–14:00 busy block + the prospect's own in-memory bookings). check/book/list/cancel/reschedule all behave as if connected — so a prospect experiences the full flow (including a slot showing as busy and getting alternatives) without any Google setup. Demo state is per-agent and resets on restart. No `GOOGLE_SERVICE_ACCOUNT_INFO` is required for the demo.
3. **At activation**, drop `demo`, the customer shares their Google Calendar with the service account's email (write access), and you set that calendar id in the agent's tool config:
   `{"google_calendar": {"calendar_id": "customer@example.com", "timezone": "Europe/Belgrade", "working_hours": "Mon-Fri 09:00-17:00", "slot_minutes": 30}}`.

If `demo` is **not** set and no `calendar_id`/`WIZARD_DEMO_CALENDAR_ID` is configured, the tools return an error (they never silently appear to work against an empty calendar).

**Contact capture**: before booking, the Tier 2 agent asks for the visitor's name **and** phone in one question and validates the phone (`create_event` re-checks: 8–15 digits, optional leading `+`; an invalid number returns `error: "invalid_phone"` and the agent asks again). On success it confirms the appointment is booked and that someone will follow up on that phone number.

**Scheduling rules**: `working_hours` and `slot_minutes` (extracted from the site during the wizard, or set per agent) are returned by `get_current_datetime`, so the agent only offers in-hours slots of the right length. Naive datetimes are normalized to the configured `timezone` before calling Google. The agent has `check_availability`, `list_events`, `create_event`, `cancel_event` and `reschedule_event` (list_events returns each event's id for cancel/move).

### Tier 3 sales setup

The Tier 3 agent shops via the native **`shop` tools** ([`shared/utils/tools/shop_tools.py`](../shared/utils/tools/shop_tools.py)),
enabled and configured through `tool_config`. The cart lives in `tool_context.state`, so it is
**session-scoped and multi-user safe** — every widget visitor gets their own cart with no shared
subprocess. During the wizard the catalog is extracted from the site and injected into the config:

```json
{"shop": {"catalog": [{"id": "item-1", "name": "Item", "price": 1500, "category": "...", "description": "..."}],
          "currency": "RSD", "shop_name": "Store",
          "vendor_email": "orders@store.com", "partner_key": "store-key"}}
```

The agent shows products as **product cards** (Add to cart → sends a message back), keeps a cart, and
**requires explicit confirmation before `place_order`**, then shows an order card. `vendor_email` and
`partner_key` are optional and independent: set `vendor_email` to email the vendor + customer on each
order, and `partner_key` to persist orders to the **Shop Orders** dashboard (grouped by partner).
Wizard trials set neither, so test orders neither email nor persist. The catalog falls back to a
generic demo catalog when none is provided. The core logic (catalog, cart, persistence, email) lives
in [`shared/utils/shop_service.py`](../shared/utils/shop_service.py).

---

## Endpoints

Public (no login; protected by a per-session token + per-IP throttle):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/wizard/embed` | The wizard iframe page (`?tier=`, `?lang=`, `?currency=`, `?partner=`, `?contact=`) |
| `GET` | `/wizard/mate-wizard.js` | Embeddable loader script |
| `GET` | `/wizard/api/tiers` | Tier catalogue + display prices (`?lang=&currency=&partner=`) |
| `POST` | `/wizard/api/session/start` | Start a wizard session → `session_token` |
| `POST` | `/wizard/api/session/step` | Save step inputs |
| `GET` | `/wizard/api/session/{token}` | Resume a session after refresh |
| `POST` | `/wizard/api/session/provision` | Provision the trial agent (rate-limited; `reprovision` to rebuild) |
| `POST` | `/wizard/api/session/abandon` | Release an unused trial on page unload (called via `sendBeacon`; no-op if a lead exists) |
| `POST` | `/wizard/api/lead` | Submit the lead |

Admin (dashboard auth):

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/dashboard/wizard-leads` | Leads page |
| `GET` | `/dashboard/wizard-pricing` | Pricing & Partners page |
| `GET` | `/dashboard/api/wizard/leads` | List leads (`?status=&search=`) |
| `PATCH` | `/dashboard/api/wizard/leads/{id}` | Update lead status (archiving frees the trial) |
| `POST` | `/dashboard/api/wizard/leads/{id}/convert` | Convert a live trial into a permanent agent |
| `GET` | `/dashboard/api/wizard/leads/{id}/snapshot` | Download the trial agent JSON snapshot |
| `GET` `PUT` | `/dashboard/api/wizard/pricing` | Get/save tier prices (`?partner=` for a partner) |
| `GET` `POST` `DELETE` | `/dashboard/api/wizard/partners` | List / upsert / delete partners |

---

## Access roles

Trial agents are created with `allowed_for_roles: ["admin", "widget"]`. Public widget visitors are
auto-assigned the **`widget`** role (their user id is scoped `widget_<keyid>_<uid>`), so they can use
only their bound, widget-enabled agent — never dashboard agents. Dashboard `user` accounts cannot use
widget/trial agents, and agents with no roles configured are admin-only. See [SSO_OAUTH.md](SSO_OAUTH.md).

## Rich cards in chat (any agent)

The embeddable chat renders **cards** from markers an agent prints in its reply — available to
**any** widget agent (appointments, free slots, Shopify cart items, custom objects). An agent emits
one or more markers; the chat parses them, strips them from the text, and renders styled cards.

Marker: `[[CARD]]{ ...json... }` (a message may contain several). Card fields:

| Field | Purpose |
|---|---|
| `type` | `appointment` \| `slot` \| `product` \| `generic` (informational) |
| `badge` | small highlighted label (e.g. "✓ Confirmed", "In stock") |
| `title` / `subtitle` | main + secondary line |
| `lines` | array of extra detail lines |
| `image` | thumbnail URL (e.g. product image) |
| `location` | shown with a 📍 |
| `ics` | `{summary, start, end, description, location}` for an "Add to calendar" download |
| `actions` | `[{label, kind, value}]` — `kind`: `message` (sends `value` back to the agent, e.g. "Book this" / "Add to cart"), `link` (opens `value`), `ics` (downloads a `.ics` built from `ics`) |

Shortcut for bookings: `[[APPOINTMENT]]{summary,start,end,html_link}` renders a confirmed-appointment
card with an **Add to calendar (.ics)** button (built client-side) and an "Open in Google Calendar" link.

Examples:

```text
[[CARD]]{"type":"slot","title":"Wed 1 Jul, 10:00","actions":[{"label":"Book this","kind":"message","value":"Book 2026-07-01T10:00"}]}
[[CARD]]{"type":"product","title":"T-shirt","subtitle":"€19","image":"https://…/t.jpg","actions":[{"label":"Add to cart","kind":"message","value":"Add T-shirt to cart"},{"label":"View","kind":"link","value":"https://…/p"}]}
[[APPOINTMENT]]{"summary":"Haircut – Pera","start":"2026-07-01T10:00:00+02:00","end":"2026-07-01T10:30:00+02:00","html_link":"https://calendar.google.com/…"}
```

## Abuse Protection

- Every write endpoint requires the opaque `session_token` issued by `/wizard/api/session/start`.
- `start` and `provision` are throttled per-IP in process.
- Trial token usage can be capped with the standard [rate limits](RATE_LIMITS.md).
- A captcha hook (`WIZARD_CAPTCHA_PROVIDER`) can be enabled for the provision step.

---

## Related Docs

- [Embeddable Chat Widget](WIDGET_INTEGRATION.md) — the chat component reused for the test step
- [Template Library](TEMPLATE_LIBRARY.md) — how tier templates are defined and imported
- [Rate Limits](RATE_LIMITS.md) — capping trial usage
